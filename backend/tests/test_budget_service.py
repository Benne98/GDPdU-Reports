"""Phase 4 — PURE unit tests for the manual-budget financial logic.

financial-metric rule (CLAUDE.md #1): each metric ships a formula + worked example
+ edge cases + test.  This file pins:

  1. seasonalize          — Σ months == annual; uniform fallback; annual=0;
                            sign-flip (negative weight) month.
  2. rollup_partners_to_position — Σ(partners)+Other == position, BOTH edit
                            directions (partner edit / total edit), Top-N remainder.
  3. sign flip            — present_to_stored / stored_to_present round-trip for
                            PL, BS asset (AR), BS credit (AP); matches the readers.
  4. budget_positions     — is_partner_driven / partner_kind_for mapping.

All DB-free.
"""
from __future__ import annotations

import math

import pytest

from unittest.mock import MagicMock

from app.services import budget_positions
from app.services import budget_service
from app.services.budget_service import (
    OTHER_PARTNER_ID,
    PartnerRow,
    _months_from_payload,
    annual_of,
    present_to_stored,
    rollup_partners_to_position,
    seasonalize,
    stored_to_present,
)


# =========================================================================== #
# 1) seasonalize
# =========================================================================== #
def test_seasonalize_sums_to_annual_with_weights():
    """Worked example: A=1200, weights [2,1,...,1] (Σ=13).
    month(1)=1200*2/13, others 1200/13; Σ == 1200."""
    weights = {1: 2.0, **{p: 1.0 for p in range(2, 13)}}
    months = seasonalize(1200.0, weights)
    assert months[1] == pytest.approx(1200.0 * 2 / 13)
    assert months[2] == pytest.approx(1200.0 / 13)
    assert sum(months.values()) == pytest.approx(1200.0, abs=1e-6)
    assert len(months) == 12


def test_seasonalize_uniform_fallback_when_no_weights():
    months = seasonalize(1200.0, None)
    assert all(v == pytest.approx(100.0) for v in months.values())
    assert sum(months.values()) == pytest.approx(1200.0, abs=1e-6)


def test_seasonalize_uniform_fallback_when_weights_sum_zero():
    months = seasonalize(1200.0, {p: 0.0 for p in range(1, 13)})
    assert all(v == pytest.approx(100.0) for v in months.values())
    assert sum(months.values()) == pytest.approx(1200.0, abs=1e-6)


def test_seasonalize_annual_zero_is_all_zero():
    months = seasonalize(0.0, {1: 5.0, 2: 3.0})
    assert all(v == 0.0 for v in months.values())
    assert sum(months.values()) == 0.0


def test_seasonalize_sign_flip_month_preserved():
    """A negative weight (a month opposing the annual sign, e.g. a credit note) is
    preserved and the split still sums to the annual.
    A=100, weights [2,-1,1,...]: Σ w = 2 + (-1) + 10*1 = 11.
      month(1)=100*2/11, month(2)=100*(-1)/11 (negative), rest 100*1/11."""
    weights = {1: 2.0, 2: -1.0, **{p: 1.0 for p in range(3, 13)}}
    months = seasonalize(100.0, weights)
    total_w = 11.0
    assert months[1] == pytest.approx(100.0 * 2 / total_w)
    assert months[2] == pytest.approx(100.0 * -1 / total_w)
    assert months[2] < 0.0  # sign flip preserved
    assert sum(months.values()) == pytest.approx(100.0, abs=1e-6)


def test_annual_of_roundtrips_seasonalize():
    months = seasonalize(987.65, {p: float(p) for p in range(1, 13)})
    assert annual_of(months) == pytest.approx(987.65, abs=1e-6)


# =========================================================================== #
# 2) rollup_partners_to_position
# =========================================================================== #
def _partners(*vals):
    return [PartnerRow(partner_id=f"P{i}", name=f"P{i}", annual=v) for i, v in enumerate(vals)]


def test_rollup_invariant_holds_basic():
    """P=1000, named A=300 B=200 → Other=500; Σ=1000."""
    roll = rollup_partners_to_position(_partners(300.0, 200.0), 1000.0)
    assert roll.other_annual == pytest.approx(500.0)
    assert roll.invariant_holds()
    assert sum(p.annual for p in roll.partners) + roll.other_annual == pytest.approx(1000.0)


def test_rollup_partner_edit_redrives_other():
    """Editing a partner (B 200→250), position unchanged → Other=450."""
    roll = rollup_partners_to_position(_partners(300.0, 250.0), 1000.0)
    assert roll.other_annual == pytest.approx(450.0)
    assert roll.invariant_holds()


def test_rollup_total_edit_moves_delta_into_other():
    """Editing the position total (1000→900), named unchanged → Other=350."""
    roll = rollup_partners_to_position(_partners(300.0, 250.0), 900.0)
    assert roll.other_annual == pytest.approx(350.0)
    # named partners untouched
    assert [p.annual for p in roll.partners] == [300.0, 250.0]
    assert roll.invariant_holds()


def test_rollup_no_named_partners_other_equals_position():
    roll = rollup_partners_to_position([], 1000.0)
    assert roll.other_annual == pytest.approx(1000.0)
    assert roll.partners == []


def test_rollup_other_negative_not_clamped():
    """Σ(named) > P → Other negative, surfaced (not clamped)."""
    roll = rollup_partners_to_position(_partners(800.0, 400.0), 1000.0)
    assert roll.other_annual == pytest.approx(-200.0)
    assert roll.invariant_holds()


def test_rollup_top_n_keeps_largest_by_abs():
    """top_n caps named partners to the largest by |annual|; the rest fold into Other."""
    rows = _partners(10.0, 500.0, 300.0, 5.0)
    roll = rollup_partners_to_position(rows, 1000.0, top_n=2)
    kept = sorted(p.annual for p in roll.partners)
    assert kept == [300.0, 500.0]
    # Other absorbs the position remainder including the dropped small partners.
    assert roll.other_annual == pytest.approx(1000.0 - 800.0)
    assert roll.invariant_holds()


# =========================================================================== #
# 3) sign flip — present_to_stored / stored_to_present
# =========================================================================== #
def test_sign_flip_pl_inverts_present():
    """PL: stored = -presented (inverse of the readers' amount*-1).
    Revenue presented +3000 → stored -3000; expense presented -500 → stored +500."""
    assert present_to_stored(3000.0, "PL", "NET_SALES") == pytest.approx(-3000.0)
    assert present_to_stored(-500.0, "PL", "COST_OF_MATERIALS") == pytest.approx(500.0)
    # round-trip
    assert stored_to_present(-3000.0, "PL", "NET_SALES") == pytest.approx(3000.0)


def test_sign_flip_bs_asset_no_flip():
    """BS asset (AR, customer-driven): presented = +stored (no flip)."""
    assert present_to_stored(100.0, "BS", "AR") == pytest.approx(100.0)
    assert stored_to_present(100.0, "BS", "AR") == pytest.approx(100.0)


def test_sign_flip_bs_credit_inverts():
    """BS credit (AP, supplier-driven): presented = -stored."""
    assert present_to_stored(100.0, "BS", "AP") == pytest.approx(-100.0)
    assert stored_to_present(-100.0, "BS", "AP") == pytest.approx(100.0)


def test_sign_flip_round_trip_all_cases():
    for stmt, code in (("PL", "NET_SALES"), ("PL", "COST_OF_MATERIALS"),
                       ("BS", "AR"), ("BS", "AP")):
        for v in (0.0, 123.45, -678.9):
            assert stored_to_present(present_to_stored(v, stmt, code), stmt, code) == pytest.approx(v)


# =========================================================================== #
# 4) budget_positions mapping
# =========================================================================== #
def test_partner_driven_mapping():
    assert budget_positions.is_partner_driven("NET_SALES")
    assert budget_positions.is_partner_driven("COST_OF_MATERIALS")
    assert budget_positions.is_partner_driven("AR")
    assert budget_positions.is_partner_driven("AP")
    assert not budget_positions.is_partner_driven("GROSS_PROFIT")
    assert not budget_positions.is_partner_driven(None)


def test_partner_kind_for():
    assert budget_positions.partner_kind_for("NET_SALES") == "customer"
    assert budget_positions.partner_kind_for("AR") == "customer"
    assert budget_positions.partner_kind_for("COST_OF_MATERIALS") == "supplier"
    assert budget_positions.partner_kind_for("AP") == "supplier"
    assert budget_positions.partner_kind_for("GROSS_PROFIT") is None


def test_resolve_partner_driven_fallback_without_session():
    resolved = budget_positions.resolve_partner_driven(None)
    assert set(resolved) == {"NET_SALES", "COST_OF_MATERIALS", "AR", "AP"}
    assert resolved["NET_SALES"].partner_kind == "customer"
    assert resolved["AP"].statement == "BS"


# =========================================================================== #
# 5) hardening — finiteness, months length, reserved-sentinel rejection
# =========================================================================== #
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_months_from_payload_rejects_non_finite_annual(bad):
    """M1: NaN/Infinity annual is rejected (→ ValueError → 422)."""
    with pytest.raises(ValueError):
        _months_from_payload(months=None, annual=bad, weights=None)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_months_from_payload_rejects_non_finite_month(bad):
    months = [100.0] * 12
    months[3] = bad
    with pytest.raises(ValueError):
        _months_from_payload(months=months, annual=None, weights=None)


def test_months_from_payload_rejects_wrong_length():
    """L3: a 13-length months array is rejected service-side."""
    with pytest.raises(ValueError):
        _months_from_payload(months=[100.0] * 13, annual=None, weights=None)


def test_months_from_payload_accepts_finite_12():
    out = _months_from_payload(months=[100.0] * 12, annual=None, weights=None)
    assert annual_of(out) == pytest.approx(1200.0)


def test_upsert_cell_rejects_reserved_partner():
    """L1: the '__OTHER__' sentinel cannot be written directly via upsert_cell."""
    with pytest.raises(ValueError):
        budget_service.upsert_cell(
            MagicMock(), statement="PL", line_code="NET_SALES", entity="",
            partner_id=OTHER_PARTNER_ID, partner_kind="customer", fiscal_year=2025,
            annual=1000.0,
        )


def test_patch_position_rejects_reserved_partner():
    """L1: the '__OTHER__' sentinel cannot be written directly via patch_position."""
    with pytest.raises(ValueError):
        budget_service.patch_position(
            MagicMock(), statement="PL", line_code="NET_SALES", entity="",
            fiscal_year=2025,
            partners=[{"partner_id": OTHER_PARTNER_ID, "annual": 500.0}],
        )
