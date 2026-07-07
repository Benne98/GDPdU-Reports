"""F4 REAL DSO/DPO regression (Phase 8, v5 pipeline) — DB-free / mock-based.

Pins the real period-flow DSO/DPO documented in ``docs/financial-logic.md`` → F4:

    DSO = |AR net-open| / gross_sales_period × days_in_period   (fact_sales)
    DPO = |AP net-open| / purchases_period   × days_in_period   (GL-derived CoM)

and the golden-safety proxy fallback (AR 30d / AP 45d) with a ``dso_source`` /
``dpo_source`` flag so a DB without the flow tables behaves exactly as the
pre-Phase-8 term proxy did.  Everything here monkeypatches the DB seams
(``_partner_view`` + the flow-query helpers) so NO Postgres is touched.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.services import opos_aging as oa


# --------------------------------------------------------------------------- #
# days_in_period
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "year, month, expected",
    [
        (2025, 12, 365),  # full non-leap fiscal year
        (2024, 12, 366),  # full leap fiscal year
        (2025, 7, 212),   # partial YTD: Jan 1 .. Jul 31
        (2025, 1, 31),    # single-month YTD
    ],
)
def test_days_in_fy_to_date(year, month, expected):
    assert oa._days_in_fy_to_date(year, month) == expected


# --------------------------------------------------------------------------- #
# real_days_value — the pure formula
# --------------------------------------------------------------------------- #
def test_real_days_value_dso_worked_example():
    # 200k AR / 1.0m sales × 365 = 73.0  (docs F4 worked example)
    assert oa.real_days_value(200_000.0, 1_000_000.0, 2025, 12) == 73.0


def test_real_days_value_dpo_worked_example():
    # 90k AP / 600k purchases × 365 = 54.75 → 54.8  (docs F4 worked example)
    assert oa.real_days_value(90_000.0, 600_000.0, 2025, 12) == 54.8


def test_real_days_value_partial_ytd():
    # 150k / 700k × 212 = 45.43 → 45.4  (docs F4 partial-YTD example)
    assert oa.real_days_value(150_000.0, 700_000.0, 2025, 7) == 45.4


def test_real_days_value_ap_sign_uses_magnitude():
    # AP signed_total_eur is a credit (negative) → magnitude, DPO stays positive.
    assert oa.real_days_value(-90_000.0, 600_000.0, 2025, 12) == 54.8


def test_real_days_value_zero_flow_returns_none():
    assert oa.real_days_value(200_000.0, 0.0, 2025, 12) is None


def test_real_days_value_none_flow_returns_none():
    assert oa.real_days_value(200_000.0, None, 2025, 12) is None


def test_real_days_value_clamped_to_max():
    # tiny flow vs large opening → clamp, never an absurd figure.
    assert oa.real_days_value(1_000_000.0, 1.0, 2025, 12) == oa._MAX_FLOW_DAYS


# --------------------------------------------------------------------------- #
# _effective_prefixes — scope resolution (deny / admin / narrow)
# --------------------------------------------------------------------------- #
class _NoNarrowSession:
    """resolve_entity_prefixes(entity) → [] (no single-entity narrow)."""


def test_effective_prefixes_admin_none(monkeypatch):
    monkeypatch.setattr(oa, "resolve_entity_prefixes", lambda s, e: [])
    assert oa._effective_prefixes(object(), None, None) is None


def test_effective_prefixes_deny_empty(monkeypatch):
    monkeypatch.setattr(oa, "resolve_entity_prefixes", lambda s, e: [])
    assert oa._effective_prefixes(object(), None, frozenset()) == []


def test_effective_prefixes_intersects_narrow(monkeypatch):
    monkeypatch.setattr(oa, "resolve_entity_prefixes", lambda s, e: ["02", "03"])
    assert oa._effective_prefixes(object(), "e", frozenset({"02", "09"})) == ["02"]


# --------------------------------------------------------------------------- #
# flow-query helpers — missing-table safety (golden-safety, no 500)
# --------------------------------------------------------------------------- #
class _RaisingSession:
    """Session whose execute() raises (e.g. missing fact_sales table)."""

    def __init__(self):
        self.rolled_back = False

    def execute(self, *a, **k):
        raise SQLAlchemyError("relation \"fact_sales\" does not exist")

    def rollback(self):
        self.rolled_back = True


def test_sales_period_swallows_db_error(monkeypatch):
    monkeypatch.setattr(oa, "resolve_entity_prefixes", lambda s, e: [])
    sess = _RaisingSession()
    assert oa._sales_period_eur(sess, 2025, 12, None, None) is None
    assert sess.rolled_back is True


def test_purchases_period_swallows_db_error(monkeypatch):
    monkeypatch.setattr(oa, "resolve_entity_prefixes", lambda s, e: [])
    sess = _RaisingSession()
    # _com_level_filter runs first and also swallows the error → None → proxy.
    assert oa._purchases_period_eur(sess, 2025, 12, None, None) is None
    assert sess.rolled_back is True


def test_flow_helpers_deny_scope_issue_no_sql(monkeypatch):
    monkeypatch.setattr(oa, "resolve_entity_prefixes", lambda s, e: [])

    class _Boom:
        def execute(self, *a, **k):  # must never be called on a deny scope
            raise AssertionError("SQL issued on fail-closed deny scope")

        def rollback(self):
            pass

    assert oa._sales_period_eur(_Boom(), 2025, 12, None, frozenset()) is None
    assert oa._purchases_period_eur(_Boom(), 2025, 12, None, frozenset()) is None


# --------------------------------------------------------------------------- #
# _side_days — real vs proxy dispatch
# --------------------------------------------------------------------------- #
def test_side_days_ar_real(monkeypatch):
    monkeypatch.setattr(oa, "_sales_period_eur", lambda *a, **k: 1_000_000.0)
    days, src = oa._side_days(object(), "AR", 2025, 12, None, 200_000.0)
    assert (days, src) == (73.0, "real")


def test_side_days_ap_real(monkeypatch):
    monkeypatch.setattr(oa, "_purchases_period_eur", lambda *a, **k: 600_000.0)
    days, src = oa._side_days(object(), "AP", 2025, 12, None, -90_000.0)
    assert (days, src) == (54.8, "real")


def test_side_days_ar_proxy_on_no_flow(monkeypatch):
    monkeypatch.setattr(oa, "_sales_period_eur", lambda *a, **k: None)
    days, src = oa._side_days(object(), "AR", 2025, 12, None, 200_000.0)
    assert (days, src) == (30.0, "proxy")


def test_side_days_ap_proxy_on_zero_flow(monkeypatch):
    monkeypatch.setattr(oa, "_purchases_period_eur", lambda *a, **k: 0.0)
    days, src = oa._side_days(object(), "AP", 2025, 12, None, -90_000.0)
    assert (days, src) == (45.0, "proxy")


# --------------------------------------------------------------------------- #
# Builder integration — real + proxy end-to-end (KPI payload)
# --------------------------------------------------------------------------- #
# The DSO/DPO numerator is the GROSS open aging basis (Σ positive-partner buckets ==
# total_open_gross), NOT the net signed_total_eur — so these fixtures put the gross
# open into a partner's FIFO buckets and set signed_total_eur to a DIFFERENT (net)
# value to prove the numerator follows the gross basis, not the net headline.
def _fake_view(gross_open_eur: float, signed_total_eur: float, name: str = "P1") -> dict:
    buckets = {b: 0.0 for b in oa._BANDS}
    buckets["not_yet_due"] = gross_open_eur  # all gross open in one bucket
    return {
        "as_of": date(2025, 12, 31),
        "aging": {
            name: {
                "total_open": gross_open_eur, "overdue_open": 0.0, "overdue_pct": 0.0,
                "credit_balance": False, "in_scatter": True, "buckets": buckets,
                "meta": {"name": name}, "open_documents": 0,
            }
        },
        "meta": {},
        "rows": [],
        "signed_total_eur": signed_total_eur,
        "open_documents": 0,
    }


def test_receivables_builder_real_dso(monkeypatch):
    # gross open 200k (numerator) vs net headline 50k — DSO MUST use the gross 200k.
    monkeypatch.setattr(oa, "_partner_view", lambda *a, **k: _fake_view(200_000.0, 50_000.0))
    monkeypatch.setattr(oa, "_sales_period_eur", lambda *a, **k: 1_000_000.0)
    res = oa.build_receivables_aging_opos(object(), 2025, 12, None)
    assert res["kpis"]["dso_days"] == 73.0  # 200000/1000000*365, not the 50k net base
    assert res["kpis"]["dso_source"] == "real"


def test_receivables_builder_proxy_fallback(monkeypatch):
    monkeypatch.setattr(oa, "_partner_view", lambda *a, **k: _fake_view(200_000.0, 50_000.0))
    monkeypatch.setattr(oa, "_sales_period_eur", lambda *a, **k: None)
    res = oa.build_receivables_aging_opos(object(), 2025, 12, None)
    assert res["kpis"]["dso_days"] == 30.0
    assert res["kpis"]["dso_source"] == "proxy"


def test_payables_builder_real_dpo(monkeypatch):
    # gross open 90k (numerator) vs net credit headline -30k — DPO MUST use gross 90k.
    monkeypatch.setattr(oa, "_partner_view", lambda *a, **k: _fake_view(90_000.0, -30_000.0))
    monkeypatch.setattr(oa, "_purchases_period_eur", lambda *a, **k: 600_000.0)
    res = oa.build_payables_aging_opos(object(), 2025, 12, None)
    assert res["kpis"]["dpo_days"] == 54.8  # 90000/600000*365
    assert res["kpis"]["dpo_source"] == "real"


def test_payables_builder_proxy_fallback(monkeypatch):
    monkeypatch.setattr(oa, "_partner_view", lambda *a, **k: _fake_view(90_000.0, -30_000.0))
    monkeypatch.setattr(oa, "_purchases_period_eur", lambda *a, **k: None)
    res = oa.build_payables_aging_opos(object(), 2025, 12, None)
    assert res["kpis"]["dpo_days"] == 45.0
    assert res["kpis"]["dpo_source"] == "proxy"


def test_dso_numerator_is_gross_not_net(monkeypatch):
    """Guard the reconciliation defect fixed in Phase 8: DSO uses the gross aging
    basis (== total_open_gross the view shows), never the net signed_total_eur."""
    view = _fake_view(15_025_000.0, 2_025_000.0)  # v5-shaped: gross 15.0M, net 2.0M
    monkeypatch.setattr(oa, "_partner_view", lambda *a, **k: view)
    monkeypatch.setattr(oa, "_sales_period_eur", lambda *a, **k: 97_591_000.0)
    res = oa.build_receivables_aging_opos(object(), 2025, 7, None)
    # 15,025,000 / 97,591,000 × 212 = 32.6 (reconciles); the net base would give 4.4.
    assert res["kpis"]["dso_days"] == 32.6
    assert res["total_open_gross"] == 15025.0  # numerator == displayed AR (kEUR)


def test_register_days_outstanding_uses_real_dso(monkeypatch):
    view = _fake_view(200_000.0, 50_000.0, name="C1")
    monkeypatch.setattr(oa, "_partner_view", lambda *a, **k: view)
    monkeypatch.setattr(oa, "_sales_period_eur", lambda *a, **k: 1_000_000.0)
    res = oa.build_receivables_customers_opos(object(), 2025, 12, None)
    assert res["register"][0]["days_outstanding"] == 73.0
    # payment_terms_days stays the due-date term (30), NOT the DSO
    assert res["register"][0]["payment_terms_days"] == 30
