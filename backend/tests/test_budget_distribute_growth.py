"""Phase 1 (Budget L4 distribution + growth input + consolidated entity-sum).

Financial-metric gated (CLAUDE.md rule #1): each pure function below has its
formula + worked example + edge cases documented in the source docstring AND in
``docs/financial-logic.md``; this file is the locking regression test.

Three groups:

  A. ``distribute_to_l4`` — split an L3 value across L4 children by historical
     share (Σ L4 == parent to 1e-6, share fidelity, uniform fallback, sign).
  B. ``resolve_input`` — all 4 modes, base=0, negative base, per-month growth.
  C. consolidated entity-sum — the golden-critical reader change: Σ(per-entity) ==
     consolidated read; a direct '' row overrides the sum; NO rows → empty (the
     golden stays byte-identical).  PURE SQL-shape assertions (DB-free) + an opt-in
     live v2 round-trip that ALWAYS cleans up so v2 ends with NO budget rows.
"""
from __future__ import annotations

import os

import pytest

from app.services import budget_service
from app.services.budget_service import (
    PERIODS,
    UNIFORM_WEIGHT,
    distribute_to_l4,
    resolve_input,
)


# =========================================================================== #
# A) distribute_to_l4  (pure)
# =========================================================================== #
def test_distribute_sum_equals_parent_by_share():
    """Σ L4 == parent; each L4 gets its historical share."""
    weights = {"Gross sales": 800.0, "Discounts": -100.0, "Freight": 300.0}
    out = distribute_to_l4(1000.0, weights)
    assert out["Gross sales"] == pytest.approx(800.0)
    assert out["Discounts"] == pytest.approx(-100.0)   # opposing weight preserved
    assert out["Freight"] == pytest.approx(300.0)
    assert sum(out.values()) == pytest.approx(1000.0, abs=1e-6)


def test_distribute_share_fidelity_unequal():
    weights = {"A": 3.0, "B": 1.0}   # 75% / 25%
    out = distribute_to_l4(400.0, weights)
    assert out["A"] == pytest.approx(300.0)
    assert out["B"] == pytest.approx(100.0)
    assert sum(out.values()) == pytest.approx(400.0, abs=1e-6)


def test_distribute_uniform_fallback_when_weights_sum_zero():
    """Σ w == 0 → uniform 1/n split; still sums to parent."""
    out = distribute_to_l4(600.0, {"A": 5.0, "B": -5.0})
    assert out["A"] == pytest.approx(300.0)
    assert out["B"] == pytest.approx(300.0)
    assert sum(out.values()) == pytest.approx(600.0, abs=1e-6)


def test_distribute_empty_weights_is_empty():
    assert distribute_to_l4(1000.0, {}) == {}


def test_distribute_zero_parent_is_all_zero():
    out = distribute_to_l4(0.0, {"A": 2.0, "B": 1.0})
    assert all(v == 0.0 for v in out.values())
    assert sum(out.values()) == pytest.approx(0.0, abs=1e-6)


def test_distribute_negative_parent_preserves_sign():
    out = distribute_to_l4(-1000.0, {"A": 3.0, "B": 1.0})
    assert out["A"] == pytest.approx(-750.0)
    assert out["B"] == pytest.approx(-250.0)
    assert sum(out.values()) == pytest.approx(-1000.0, abs=1e-6)


# =========================================================================== #
# B) resolve_input  (pure)
# =========================================================================== #
def test_resolve_absolute_annual_seasonalizes():
    out = resolve_input("absolute_annual", 1200.0, weights=None)
    assert all(v == pytest.approx(100.0) for v in out.values())
    assert sum(out.values()) == pytest.approx(1200.0, abs=1e-6)


def test_resolve_absolute_annual_with_weights():
    weights = {p: (2.0 if p == 1 else 1.0) for p in PERIODS}  # Σ=13
    out = resolve_input("absolute_annual", 1300.0, weights=weights)
    assert out[1] == pytest.approx(200.0)
    assert out[2] == pytest.approx(100.0)
    assert sum(out.values()) == pytest.approx(1300.0, abs=1e-6)


def test_resolve_absolute_monthly_as_is():
    vals = [float(i) for i in range(1, 13)]
    out = resolve_input("absolute_monthly", vals)
    assert [out[p] for p in PERIODS] == vals


def test_resolve_growth_annual_against_base():
    out = resolve_input("growth_annual", 0.10, base={"annual": 1000.0}, weights=None)
    assert sum(out.values()) == pytest.approx(1100.0, abs=1e-6)


def test_resolve_growth_annual_base_zero_yields_zero():
    """Growth off a ZERO base resolves to 0 (documented note), not the rate."""
    out = resolve_input("growth_annual", 0.25, base={"annual": 0.0})
    assert sum(out.values()) == pytest.approx(0.0, abs=1e-6)


def test_resolve_growth_annual_negative_base_preserves_sign():
    out = resolve_input("growth_annual", 0.10, base={"annual": -1000.0})
    assert sum(out.values()) == pytest.approx(-1100.0, abs=1e-6)


def test_resolve_growth_monthly_scalar_rate():
    base_months = {p: 100.0 * p for p in PERIODS}
    out = resolve_input("growth_monthly", 0.05, base={"months": base_months})
    for p in PERIODS:
        assert out[p] == pytest.approx(100.0 * p * 1.05)


def test_resolve_growth_monthly_per_month_rates():
    base_months = {p: 100.0 for p in PERIODS}
    rates = [0.0] * 12
    rates[0] = 0.10  # +10% in Jan only
    out = resolve_input("growth_monthly", rates, base={"months": base_months})
    assert out[1] == pytest.approx(110.0)
    assert out[2] == pytest.approx(100.0)


def test_resolve_growth_monthly_missing_base_month_is_zero():
    out = resolve_input("growth_monthly", 0.10, base={"months": {1: 100.0}})
    assert out[1] == pytest.approx(110.0)
    assert out[2] == pytest.approx(0.0)   # no base for Feb → 0


def test_resolve_unknown_mode_raises():
    with pytest.raises(ValueError):
        resolve_input("nonsense", 1.0)


# =========================================================================== #
# C) consolidated entity-sum — golden-critical reader change (PURE SQL shape)
# =========================================================================== #
from app.services.fin_compat_sql import position_plan_grain_sql


def test_consolidated_sql_sums_per_entity_else_override():
    """Consolidated PL position SQL: '' row OR per-entity rows where no '' exists."""
    sql, params = position_plan_grain_sql(2025, 6, "", "PL")
    assert ":ep" not in sql
    # '' rows always participate; per-entity rows participate only when no '' row.
    assert "p.entity_prefix = ''" in sql
    assert "p.entity_prefix <> ''" in sql
    assert "NOT EXISTS" in sql
    assert "c.entity_prefix = ''" in sql
    # Golden-safety: no budget rows → WHERE matches nothing → empty result set.
    assert "scenario = :scenario" in sql and params["scenario"] == "budget"


def test_per_entity_sql_unchanged_precedence():
    """Per-entity view still: per-entity rows + '' only where no per-entity row."""
    sql, params = position_plan_grain_sql(2025, 6, "AND l.entity_prefix = 'AT'", "PL")
    assert params["ep"] == "AT"
    assert "p.entity_prefix = :ep" in sql
    assert "e.entity_prefix = :ep" in sql   # the NOT EXISTS still keys on :ep


# --------------------------------------------------------------------------- #
# C2) consolidated entity-sum — live v2 round-trip (opt-in; cleans up)
# --------------------------------------------------------------------------- #
def _v2_session_or_skip():
    from sqlalchemy import text

    from app.db import SessionLocal

    try:
        s = SessionLocal()
        s.execute(text("SELECT level_4 FROM fact_position_plan LIMIT 1"))
        return s
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"v2 budget DB not reachable / column absent: {exc}")


_FY = 2025
_LC = "NET_SALES"


@pytest.mark.skipif(
    os.getenv("DB_NAME", "Finssentials") != "finssentials_v2",
    reason="entity-sum round-trip runs only against finssentials_v2 (set DB_NAME)",
)
class TestEntitySumRoundTripV2:
    """Σ(per-entity) == consolidated read; '' override beats the sum; none → empty.

    ALWAYS cleans up in finally so v2 ends with NO budget rows (golden EQUIVALENT).
    """

    def _clean(self, session):
        from sqlalchemy import text
        session.execute(
            text("DELETE FROM fact_position_plan WHERE scenario='budget' "
                 "AND statement='PL' AND line_code=:lc AND fiscal_year=:fy"),
            {"lc": _LC, "fy": _FY},
        )
        session.commit()

    def _consolidated_annual(self, session):
        saved = budget_service._read_budget_position_months(
            session, statement="PL", fiscal_year=_FY, entity_prefix=None
        )
        months = saved.get(_LC, {}).get("", {})
        return sum(months.values())

    def test_no_rows_consolidated_is_empty(self):
        session = _v2_session_or_skip()
        try:
            self._clean(session)
            saved = budget_service._read_budget_position_months(
                session, statement="PL", fiscal_year=_FY, entity_prefix=None
            )
            assert saved.get(_LC) is None  # byte-identical: no overlay at all
        finally:
            self._clean(session)
            session.close()

    def test_sum_of_entities_equals_consolidated(self):
        session = _v2_session_or_skip()
        try:
            self._clean(session)
            # Two per-entity plans, NO '' row → consolidated = Σ entities.
            budget_service.upsert_cell(
                session, statement="PL", line_code=_LC, entity="01",
                partner_id=None, partner_kind=None, fiscal_year=_FY,
                annual=12000.0, updated_by="test@finssentials",
            )
            budget_service.upsert_cell(
                session, statement="PL", line_code=_LC, entity="02",
                partner_id=None, partner_kind=None, fiscal_year=_FY,
                annual=8000.0, updated_by="test@finssentials",
            )
            assert self._consolidated_annual(session) == pytest.approx(20000.0, abs=1e-2)
        finally:
            self._clean(session)
            session.close()

    def test_direct_consolidated_overrides_sum(self):
        session = _v2_session_or_skip()
        try:
            self._clean(session)
            budget_service.upsert_cell(
                session, statement="PL", line_code=_LC, entity="01",
                partner_id=None, partner_kind=None, fiscal_year=_FY,
                annual=12000.0, updated_by="test@finssentials",
            )
            budget_service.upsert_cell(
                session, statement="PL", line_code=_LC, entity="02",
                partner_id=None, partner_kind=None, fiscal_year=_FY,
                annual=8000.0, updated_by="test@finssentials",
            )
            # Direct consolidated ('') row → overrides the Σ entities.
            budget_service.upsert_cell(
                session, statement="PL", line_code=_LC, entity="",
                partner_id=None, partner_kind=None, fiscal_year=_FY,
                annual=25000.0, updated_by="test@finssentials",
            )
            assert self._consolidated_annual(session) == pytest.approx(25000.0, abs=1e-2)
        finally:
            self._clean(session)
            session.close()

    def test_top_down_distribute_l4_sums_to_parent(self):
        """upsert_cell(distribute_l4=True) writes L4 rows whose SUM == parent and the
        L3 '' row is XOR-cleared (no double count); falls back to '' when no history."""
        from sqlalchemy import text
        session = _v2_session_or_skip()
        try:
            self._clean(session)
            res = budget_service.upsert_cell(
                session, statement="PL", line_code=_LC, entity="",
                partner_id=None, partner_kind=None, fiscal_year=_FY,
                annual=12000.0, distribute_l4=True, updated_by="test@finssentials",
            )
            # If the seed DB has L4 history for Net sales we get L4 rows; else the
            # fallback writes a single L3 ('') row.  Either way the position total
            # reads back to the parent and there is no double count.
            assert self._consolidated_annual(session) == pytest.approx(12000.0, abs=1e-2)
            # XOR: never both an L3 '' row AND L4 rows for the same scope.
            n_l3 = int(session.execute(
                text("SELECT COUNT(*) FROM fact_position_plan WHERE scenario='budget' "
                     "AND statement='PL' AND line_code=:lc AND fiscal_year=:fy "
                     "AND entity_prefix='' AND level_4='' AND partner_id=''"),
                {"lc": _LC, "fy": _FY},
            ).scalar() or 0)
            n_l4 = int(session.execute(
                text("SELECT COUNT(*) FROM fact_position_plan WHERE scenario='budget' "
                     "AND statement='PL' AND line_code=:lc AND fiscal_year=:fy "
                     "AND entity_prefix='' AND level_4<>''"),
                {"lc": _LC, "fy": _FY},
            ).scalar() or 0)
            assert (n_l3 == 0) != (n_l4 == 0), "exactly one level must exist (XOR)"
        finally:
            self._clean(session)
            session.close()
