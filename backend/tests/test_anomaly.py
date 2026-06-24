"""DB-free tests for the deterministic anomaly engine (reporting-v2 Phase 6).

Project hard rule #1 (no financial logic without proof): the anomaly module adds
no new monetary formula — it REUSES the narrative-core thresholds
(``MOM_FLOOR_EUR``, ``MOM_PCT_OF_CM``, ``YOY_REL_FACTOR``,
``ACCOUNT_CONCENTRATION_PCT``, ``BOOKING_SHARE_PCT``) and facts.  These tests pin
each anomaly KIND, the SEVERITY banding, threshold RESPECT (nothing fires below
the floor) and the DETERMINISTIC ORDER, all with tiny worked examples.

No DB, no network — statements are synthetic row trees; the public
``detect_anomalies`` path is exercised with the statement builders monkeypatched.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from app.services import anomaly
from app.services import fin_compat_narrative_core as core


# ---------------------------------------------------------------------------
# Synthetic-row helper (same shape the compat builders emit)
# ---------------------------------------------------------------------------
def _line(
    code: str,
    label: str,
    cm: float,
    pm: float,
    py_cm: float = 0.0,
    *,
    row_kind: str = "line",
    children: Optional[list] = None,
) -> dict[str, Any]:
    return {
        "line_code": code,
        "label": label,
        "row_kind": row_kind,
        "kpi_code": "",
        "amounts": {"py_cm": py_cm, "pm": pm, "cm": cm, "ytd": 0.0, "ytd_py": 0.0},
        "deltas": {"mom": cm - pm, "yoy": cm - py_cm, "ytd": 0.0},
        "children": children or [],
        "accounts": [],
    }


# ===========================================================================
# Severity banding
# ===========================================================================
def test_severity_bands_use_mom_floor_multiples():
    """high >= 10x floor (300k); medium >= 3x (90k); low otherwise."""
    assert anomaly._severity_for_magnitude(10 * core.MOM_FLOOR_EUR) == "high"
    assert anomaly._severity_for_magnitude(3 * core.MOM_FLOOR_EUR) == "medium"
    assert anomaly._severity_for_magnitude(2.99 * core.MOM_FLOOR_EUR) == "low"
    assert anomaly._severity_for_magnitude(-10 * core.MOM_FLOOR_EUR) == "high"  # abs


# ===========================================================================
# MoM swing
# ===========================================================================
def test_mom_swing_fires_above_floor():
    """cm 520k pm 400k → mom +120k >= max(30k, 3%*520k=15.6k) → medium MoM swing."""
    rows = [_line("NET_SALES", "Net sales", 520_000, 400_000, 470_000)]
    anoms = anomaly.detect_for_statement("pl", rows, base_cm=520_000)
    mom = [a for a in anoms if a.kind == "mom_swing"]
    assert len(mom) == 1
    assert mom[0].delta == pytest.approx(120_000)
    assert mom[0].magnitude_eur == pytest.approx(120_000)
    assert mom[0].severity == "medium"  # 120k in [90k, 300k)


def test_mom_swing_silent_below_floor():
    """Edge: cm 5m, mom +20k < max(30k, 3%*5m=150k) → no MoM swing."""
    rows = [_line("BIG", "Big line", 5_000_000, 4_980_000, 4_980_000)]
    anoms = anomaly.detect_for_statement("pl", rows, base_cm=5_000_000)
    assert not [a for a in anoms if a.kind == "mom_swing"]


# ===========================================================================
# YoY swing
# ===========================================================================
def test_yoy_swing_fires_and_is_independent_of_mom():
    """cm 500k, pm 495k (no MoM), py_cm 350k → yoy +150k fires; no MoM swing."""
    rows = [_line("REV", "Revenue", 500_000, 495_000, 350_000)]
    anoms = anomaly.detect_for_statement("pl", rows, base_cm=500_000)
    kinds = {a.kind for a in anoms}
    assert "yoy_swing" in kinds
    assert "mom_swing" not in kinds  # mom 5k below floor
    yoy = next(a for a in anoms if a.kind == "yoy_swing")
    assert yoy.delta == pytest.approx(150_000)


# ===========================================================================
# Sign flip
# ===========================================================================
def test_sign_flip_vs_prior_month():
    """pm +40k → cm -50k: opposite signs, crossing 90k >= 30k floor → sign_flip."""
    rows = [_line("EBIT", "EBIT", -50_000, 40_000, -20_000)]
    anoms = anomaly.detect_for_statement("pl", rows, base_cm=1_000_000)
    flips = [a for a in anoms if a.kind == "sign_flip"]
    assert len(flips) == 1
    assert flips[0].magnitude_eur == pytest.approx(90_000)  # |cm| + |pm|


def test_sign_flip_silent_when_tiny_crossing():
    """Edge: +5k → -3k crossing 8k < 30k floor → no sign_flip."""
    rows = [_line("X", "X", -3_000, 5_000, -3_000)]
    anoms = anomaly.detect_for_statement("pl", rows, base_cm=1_000_000)
    assert not [a for a in anoms if a.kind == "sign_flip"]


def test_no_sign_flip_when_same_sign():
    """Both positive → never a sign flip even with a big move."""
    rows = [_line("Y", "Y", 200_000, 50_000, 40_000)]
    anoms = anomaly.detect_for_statement("pl", rows, base_cm=1_000_000)
    assert not [a for a in anoms if a.kind == "sign_flip"]


# ===========================================================================
# Balance break (BS only)
# ===========================================================================
def test_balance_break_fires_when_assets_ne_eq_liab():
    rows = [
        _line("BS_GRANDTOTAL_ASSETS", "Total assets", 1_000_000, 950_000, row_kind="subtotal"),
        _line("BS_GRANDTOTAL_EQ_LIAB", "Total equity & liabilities", 900_000, 940_000, row_kind="subtotal"),
    ]
    anoms = anomaly.detect_for_statement("bs", rows, base_cm=1_000_000)
    breaks = [a for a in anoms if a.kind == "balance_break"]
    assert len(breaks) == 1
    assert breaks[0].magnitude_eur == pytest.approx(100_000)  # 1.0m - 0.9m
    assert breaks[0].severity == "high"


def test_balance_break_silent_when_balanced():
    rows = [
        _line("BS_GRANDTOTAL_ASSETS", "Total assets", 1_000_000, 950_000, row_kind="subtotal"),
        _line("BS_GRANDTOTAL_EQ_LIAB", "Total equity & liabilities", 1_000_000, 950_000, row_kind="subtotal"),
    ]
    anoms = anomaly.detect_for_statement("bs", rows, base_cm=1_000_000)
    assert not [a for a in anoms if a.kind == "balance_break"]


def test_balance_break_only_for_bs():
    """A PL statement is never checked for a balance break (no grand-totals)."""
    rows = [_line("NET_SALES", "Net sales", 520_000, 400_000)]
    anoms = anomaly.detect_for_statement("pl", rows, base_cm=520_000)
    assert not [a for a in anoms if a.kind == "balance_break"]


# ===========================================================================
# GL concentration (reuses core.gl_concentration_from_detail via callback)
# ===========================================================================
def test_gl_concentration_fires_for_dominant_account():
    """A material mover whose detail shows one account >= 50% of the move."""
    rows = [_line("MAT", "Materials", 300_000, 150_000, 160_000)]  # mom +150k

    def _detail(line_code, signed_mom_eur):
        return {
            "accounts": [
                {"account_name": "Steel", "gl_account_id": "400100",
                 "balance_cm": 300, "balance_pm": 150, "delta": 150},
            ],
            "top_bookings": [{"amount": 140, "line_note": "Steel delivery"}],
        }

    anoms = anomaly.detect_for_statement(
        "pl", rows, base_cm=520_000, gl_detail_fn=_detail,
    )
    conc = [a for a in anoms if a.kind == "gl_concentration"]
    assert len(conc) == 1
    assert conc[0].severity == "high"
    assert "account 400100 *Steel*" in conc[0].description


def test_gl_concentration_silent_when_diversified():
    """Edge: a material move spread across many accounts (no single >= 50%)."""
    rows = [_line("MAT", "Materials", 300_000, 150_000, 160_000)]

    def _detail(line_code, signed_mom_eur):
        return {
            "accounts": [
                {"account_name": "A", "gl_account_id": "1", "balance_cm": 100, "balance_pm": 50, "delta": 50},
                {"account_name": "B", "gl_account_id": "2", "balance_cm": 100, "balance_pm": 50, "delta": 50},
                {"account_name": "C", "gl_account_id": "3", "balance_cm": 100, "balance_pm": 50, "delta": 50},
            ],
            "top_bookings": [{"amount": 10, "line_note": "small"}],
        }

    anoms = anomaly.detect_for_statement(
        "pl", rows, base_cm=520_000, gl_detail_fn=_detail,
    )
    # 50/150 = 33% < 50% account threshold; booking 10/150 = 6.7% < 25% → none
    assert not [a for a in anoms if a.kind == "gl_concentration"]


# ===========================================================================
# Deterministic ordering
# ===========================================================================
def test_deterministic_order_by_severity_then_magnitude():
    """high before medium before low; within a band, larger magnitude first."""
    rows = [
        _line("LOW", "Low", 80_000, 40_000, 40_000),       # mom +40k → low
        _line("HIGH", "High", 600_000, 100_000, 100_000),  # mom +500k → high
        _line("MED", "Med", 250_000, 130_000, 130_000),    # mom +120k → medium
    ]
    anoms = anomaly.detect_for_statement("pl", rows, base_cm=1_000_000)
    mom = [a for a in anoms if a.kind == "mom_swing"]
    mom.sort(key=lambda a: a.sort_key())
    assert [a.line_code for a in mom] == ["HIGH", "MED", "LOW"]


def test_sort_key_is_stable_for_equal_magnitude():
    """Tie-break by statement, line_code, kind so order is fully deterministic."""
    a1 = anomaly.Anomaly("pl", "B", "B", "mom_swing", "high", "month", 1, 1, 100, "")
    a2 = anomaly.Anomaly("pl", "A", "A", "mom_swing", "high", "month", 1, 1, 100, "")
    ordered = sorted([a1, a2], key=lambda a: a.sort_key())
    assert [a.line_code for a in ordered] == ["A", "B"]


# ===========================================================================
# Public detect_anomalies path (statement builders monkeypatched)
# ===========================================================================
def _stmt(rows: list[dict]) -> dict[str, Any]:
    return {"rows": rows, "col_labels": {"cm": "Jun25", "pm": "May25"}}


def test_detect_anomalies_aggregates_all_statements(monkeypatch):
    pl = _stmt([_line("NET_SALES", "Net sales", 520_000, 400_000, 470_000)])
    bs = _stmt([
        _line("BS_GRANDTOTAL_ASSETS", "Total assets", 1_000_000, 950_000, row_kind="subtotal"),
        _line("BS_GRANDTOTAL_EQ_LIAB", "Total equity & liabilities", 850_000, 940_000, row_kind="subtotal"),
    ])
    empty = _stmt([])

    monkeypatch.setattr(anomaly, "detect_anomalies", anomaly.detect_anomalies)  # keep real
    import app.services.fin_compat_pl as pl_mod
    import app.services.fin_compat_bs as bs_mod
    import app.services.fin_compat_wc as wc_mod
    import app.services.fin_compat_cf as cf_mod
    monkeypatch.setattr(pl_mod, "build_pl_statement_compat", lambda *a, **k: pl)
    monkeypatch.setattr(bs_mod, "build_bs_statement_compat", lambda *a, **k: bs)
    monkeypatch.setattr(wc_mod, "build_wc_statement_compat", lambda *a, **k: empty)
    monkeypatch.setattr(cf_mod, "build_cf_statement_compat", lambda *a, **k: empty)

    period = {"grain": "month", "year": 2025, "month": 6,
              "iso_year": None, "iso_week": None}
    anoms = anomaly.detect_anomalies(
        MagicMock(), period, entity=None, with_gl_concentration=False,
    )
    kinds = {(a.statement, a.kind) for a in anoms}
    assert ("pl", "mom_swing") in kinds
    assert ("bs", "balance_break") in kinds
    # period stamped on every anomaly
    assert all(a.year == 2025 and a.month == 6 for a in anoms)
    # high (balance break / 150k mom) sorts before any low
    assert anoms[0].severity == "high"


# ===========================================================================
# Phase 0 (hierarchical rework): detect_anomalies is PL + BS ONLY (WC/CF out)
# ===========================================================================
def test_detect_anomalies_scans_only_pl_and_bs(monkeypatch):
    """WC/CF builders must NOT be consulted and produce no anomalies — even when
    they would otherwise emit a material mover.  Only pl/bs statements appear."""
    import app.services.fin_compat_pl as pl_mod
    import app.services.fin_compat_bs as bs_mod

    pl = _stmt([_line("NET_SALES", "Net sales", 520_000, 400_000, 470_000)])
    bs = _stmt([
        _line("BS_GRANDTOTAL_ASSETS", "Total assets", 1_000_000, 950_000, row_kind="subtotal"),
        _line("BS_GRANDTOTAL_EQ_LIAB", "Total equity & liabilities", 850_000, 940_000, row_kind="subtotal"),
    ])
    monkeypatch.setattr(pl_mod, "build_pl_statement_compat", lambda *a, **k: pl)
    monkeypatch.setattr(bs_mod, "build_bs_statement_compat", lambda *a, **k: bs)

    # Guards: a WC/CF builder call would be a regression — fail loudly if invoked.
    import app.services.fin_compat_wc as wc_mod
    import app.services.fin_compat_cf as cf_mod

    def _boom(*a, **k):  # pragma: no cover - only fires on regression
        raise AssertionError("WC/CF statement builder must not be called")

    monkeypatch.setattr(wc_mod, "build_wc_statement_compat", _boom)
    monkeypatch.setattr(cf_mod, "build_cf_statement_compat", _boom)

    period = {"grain": "month", "year": 2025, "month": 6,
              "iso_year": None, "iso_week": None}
    anoms = anomaly.detect_anomalies(
        MagicMock(), period, entity=None, with_gl_concentration=False,
    )
    statements = {a.statement for a in anoms}
    assert statements <= {"pl", "bs"}
    assert "wc" not in statements and "cf" not in statements


# ===========================================================================
# Phase 0: latest_anchor returns the newest (year, period) from the span
# ===========================================================================
def test_latest_anchor_returns_last_span_month(monkeypatch):
    import app.services.gl_analysis_common as gac

    monkeypatch.setattr(
        gac, "discover_month_span",
        lambda session: [(2023, 11), (2024, 1), (2024, 2)],
    )
    assert anomaly.latest_anchor(MagicMock()) == (2024, 2)


def test_latest_anchor_empty_ledger_is_none(monkeypatch):
    import app.services.gl_analysis_common as gac

    monkeypatch.setattr(gac, "discover_month_span", lambda session: [])
    assert anomaly.latest_anchor(MagicMock()) is None


# ===========================================================================
# Performance guard: the GL-concentration path must take the bounded,
# concentration-only line-detail fast path (no N+1 timeline rebuilds).  This pins
# the fix for the slow anomalies endpoint; a regression that drops the flag would
# silently bring the N+1 explosion back.  Phase 0 scope is PL + BS only, so the
# guard is pinned on the PL line-detail path (WC's prebuilt_rows fast path is no
# longer reachable now that WC is out of detect_anomalies).
# ===========================================================================
def test_gl_concentration_uses_bounded_concentration_only_path(monkeypatch):
    """Every build_*_line_detail call from the anomaly engine must pass
    ``concentration_only=True``.  The concentration anomaly must still fire."""
    # A material PL mover whose detail shows one dominant account (>= 50%).
    pl_rows = [_line("MAT", "Materials", 300_000, 150_000, 160_000)]  # mom +150k

    import app.services.fin_compat_pl as pl_mod
    import app.services.fin_compat_bs as bs_mod
    import app.services.fin_compat_line_detail as pl_ld
    monkeypatch.setattr(pl_mod, "build_pl_statement_compat", lambda *a, **k: _stmt(pl_rows))
    monkeypatch.setattr(bs_mod, "build_bs_statement_compat", lambda *a, **k: _stmt([]))

    calls: list[dict[str, Any]] = []

    def _detail(stmt_name):
        def _fn(session, line_code, year, month, entity, **kwargs):
            calls.append({"stmt": stmt_name, "line_code": line_code, **kwargs})
            return {
                "accounts": [
                    {"account_name": "Steel", "gl_account_id": "400100",
                     "balance_cm": 300, "balance_pm": 150, "delta": 150},
                ],
                "top_bookings": [{"amount": 140, "line_note": "Steel delivery"}],
            }
        return _fn

    monkeypatch.setattr(pl_ld, "build_pl_line_detail", _detail("pl"))
    monkeypatch.setattr(bs_mod, "build_bs_line_detail", _detail("bs"))

    period = {"grain": "month", "year": 2025, "month": 7,
              "iso_year": None, "iso_week": None}
    anoms = anomaly.detect_anomalies(MagicMock(), period, entity="all")

    # The line-detail builder was invoked for the material PL mover ...
    assert calls, "expected at least one bounded line-detail call"
    pl_calls = [c for c in calls if c["stmt"] == "pl"]
    assert pl_calls, "PL concentration path must call build_pl_line_detail"

    # ... in the bounded, concentration-only mode (no timeline N+1).
    assert all(c.get("concentration_only") is True for c in calls), (
        "every anomaly line-detail call must pass concentration_only=True"
    )

    # Behaviour preserved: the dominant-account concentration anomaly still fires.
    conc = [a for a in anoms if a.kind == "gl_concentration"]
    assert conc, "concentration anomaly must still fire on the fast path"
    assert "account 400100 *Steel*" in conc[0].description


# ===========================================================================
# M2 regression: GET /api/v1/financials/anomalies must be PURE READ
# (no write to fact_anomaly reachable via GET, with or without ?cache=true)
# ===========================================================================
class TestAnomaliesGetIsPureRead:
    """The anomalies GET previously called ``cache_anomalies`` (DELETE+INSERT+commit
    on fact_anomaly) when ``?cache=true`` — a write reachable by ANY authenticated
    user via GET.  The fix removes the write from the GET path entirely.  These
    DB-free TestClient tests pin that:
      - the route exposes NO ``cache`` query param (it is silently ignored), and
      - ``cache_anomalies`` is NEVER called from the GET, even with ?cache=true.
    """

    @staticmethod
    def _client():
        from fastapi.testclient import TestClient
        from app.auth import User, current_user
        from app.db import get_session
        from app.main import app

        app.dependency_overrides[get_session] = lambda: MagicMock()
        app.dependency_overrides[current_user] = lambda: User(
            user_id=1, email="t@finssentials.com", display_name="T", is_admin=False,
        )
        return TestClient(app, raise_server_exceptions=False)

    def test_get_does_not_call_cache_even_with_cache_true(self, monkeypatch):
        import app.routers.financials_compat as fc

        monkeypatch.setattr(fc, "detect_anomalies", lambda *a, **k: [])
        # If the router still imported/called cache_anomalies, this spy would fire.
        spy = MagicMock()
        import app.services.anomaly as anomaly_svc
        monkeypatch.setattr(anomaly_svc, "cache_anomalies", spy, raising=False)

        client = self._client()
        try:
            resp = client.get(
                "/api/v1/financials/anomalies",
                params={"year": 2025, "month": 6, "cache": "true"},
            )
        finally:
            from app.main import app
            app.dependency_overrides.clear()

        assert resp.status_code == 200, resp.text
        assert resp.json()["anomalies"] == []
        spy.assert_not_called()  # PURE READ: no write reachable via GET

    def test_router_does_not_reference_cache_anomalies(self):
        """Defense in depth: the compat router module must not bind cache_anomalies."""
        import app.routers.financials_compat as fc

        assert not hasattr(fc, "cache_anomalies"), (
            "cache_anomalies must not be importable into the GET router"
        )


def test_anomaly_to_api_contract():
    a = anomaly.Anomaly(
        statement="pl", line_code="NET_SALES", label="Net sales",
        kind="mom_swing", severity="medium", period_grain="month",
        value=520_000, delta=120_000, magnitude_eur=120_000,
        description="Net sales moved +€120k.", entity=None,
        year=2025, month=6,
    )
    period = {"grain": "month", "year": 2025, "month": 6}
    api = anomaly.anomaly_to_api(a, period)
    assert set(api) == {
        "id", "statement", "line_code", "label", "kind", "severity",
        "period_label", "entity", "value", "delta", "magnitude_eur", "description",
    }
    assert api["period_label"] == "2025-06"
    assert api["entity"] == "all"
