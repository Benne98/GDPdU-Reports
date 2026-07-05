"""Area 2 (Overview v2) — liquidity-available ("Zeitverkauf") sign-off + service.

APPROVED net-new monetary logic (owner sign-off, decision #3).  Pins the AR
collectibility haircut ladder + CollectibleAR + LiquidityAvailable AND the
Phase-5 service ``app.services.liquidity`` against the signed-off contract.

The haircut is a CONFIG table (``AR_HAIRCUT_LADDER``), keyed by the canonical
``app.services.gl_aging.AR_BANDS`` bucket ids — never inline constants, never an
invented band name.

=== WORKED EXAMPLE ==========================================================
  AR bands (not_yet_due / 1-30 / 31-60 / 61-90 / 91-180 / >180) = 400/100/50/40/20/10
  haircut                                                       = 0/0/.10/.25/.50/1.0
    CollectibleAR = 400 + 100 + 50·.9 + 40·.75 + 20·.5 + 10·0
                  = 400 + 100 + 45 + 30 + 10 + 0 = 585
  Cash = 120, OutstandingAP = 300
    LiquidityAvailable = 120 + 585 − 300 = 405 kEUR
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.liquidity import (
    AR_HAIRCUT_LADDER,
    build_liquidity_available,
    collectible_breakdown,
    compute_collectible_ar,
    compute_liquidity_available,
)

WORKED_BANDS = {
    "not_yet_due": 400.0, "overdue_1_30": 100.0, "overdue_31_60": 50.0,
    "overdue_61_90": 40.0, "overdue_91_180": 20.0, "overdue_over_180": 10.0,
}


# ---------------------------------------------------------------------------
# Row helper (SQLAlchemy Row-like with _mapping) — house convention
# ---------------------------------------------------------------------------
class _DictRow:
    def __init__(self, d: dict):
        self._mapping = d
        for k, v in d.items():
            setattr(self, k, v)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._mapping.values())[key]
        return self._mapping[key]

    def get(self, key, default=None):
        return self._mapping.get(key, default)


# ===========================================================================
# (1) Ladder contract — config, canonical bands, monotonic
# ===========================================================================
class TestLadderContract:

    def test_ladder_is_config_not_inline(self):
        # It is a module-level dict keyed by the canonical band ids.
        assert isinstance(AR_HAIRCUT_LADDER, dict)
        assert AR_HAIRCUT_LADDER == {
            "not_yet_due": 0.00, "overdue_1_30": 0.00, "overdue_31_60": 0.10,
            "overdue_61_90": 0.25, "overdue_91_180": 0.50, "overdue_over_180": 1.00,
        }

    def test_ladder_covers_canonical_bands_exactly(self):
        from app.services.gl_aging import AR_BANDS
        canonical = {b for b, _ in AR_BANDS}
        assert canonical == set(AR_HAIRCUT_LADDER)

    def test_ladder_is_monotonic_nondecreasing(self):
        from app.services.gl_aging import AR_BANDS
        order = [b for b, _ in AR_BANDS]
        vals = [AR_HAIRCUT_LADDER[b] for b in order]
        assert vals == sorted(vals)
        assert vals[0] == 0.0 and vals[-1] == 1.0


# ===========================================================================
# (2) Worked example — CollectibleAR 585 / LiquidityAvailable 405 kEUR
# ===========================================================================
class TestWorkedExample:

    def test_collectible_ar(self):
        assert compute_collectible_ar(WORKED_BANDS) == pytest.approx(585.0, abs=1e-6)

    def test_liquidity_available(self):
        got = compute_liquidity_available(
            cash=120.0, ar_bands=WORKED_BANDS, outstanding_ap=300.0,
        )
        assert got == pytest.approx(405.0, abs=1e-6)

    def test_breakdown_per_band_haircut(self):
        rows, raw, coll = collectible_breakdown(WORKED_BANDS)
        by = {r["band"]: r for r in rows}
        assert by["not_yet_due"]["collectible"] == 400.0
        assert by["overdue_31_60"]["collectible"] == 45.0   # 50·(1−.10)
        assert by["overdue_61_90"]["collectible"] == 30.0   # 40·(1−.25)
        assert by["overdue_91_180"]["collectible"] == 10.0  # 20·(1−.50)
        assert by["overdue_over_180"]["collectible"] == 0.0  # 10·(1−1.0)
        assert raw == 620.0 and coll == 585.0
        # canonical band order preserved
        assert [r["band"] for r in rows] == [
            "not_yet_due", "overdue_1_30", "overdue_31_60",
            "overdue_61_90", "overdue_91_180", "overdue_over_180",
        ]


# ===========================================================================
# (3) Edge cases — pure helpers
# ===========================================================================
class TestEdges:

    def test_empty_ar(self):
        assert compute_collectible_ar({}) == 0.0
        assert compute_liquidity_available(
            cash=120.0, ar_bands={}, outstanding_ap=300.0) == -180.0

    def test_all_over_180_fully_haircut(self):
        assert compute_collectible_ar({"overdue_over_180": 999.0}) == 0.0

    def test_credit_balance_band_floored_and_flagged(self):
        rows, raw, coll = collectible_breakdown({"not_yet_due": -50.0})
        by = {r["band"]: r for r in rows}
        assert by["not_yet_due"]["credit_flag"] is True
        assert by["not_yet_due"]["collectible"] == 0.0  # not negative
        assert coll == 0.0
        # a credit band never drags CollectibleAR below zero
        assert compute_collectible_ar({"not_yet_due": -50.0, "overdue_1_30": 10.0}) == 10.0

    def test_negative_cash_overdraft_flows_through(self):
        # overdraft is NOT floored: liquidity may be negative
        assert compute_liquidity_available(
            cash=-50.0, ar_bands={"not_yet_due": 10.0}, outstanding_ap=0.0) == -40.0

    def test_missing_ap_treated_as_zero(self):
        assert compute_liquidity_available(
            cash=100.0, ar_bands={"not_yet_due": 50.0}, outstanding_ap=0.0) == 150.0

    def test_unknown_band_raises(self):
        with pytest.raises(KeyError):
            compute_collectible_ar({"overdue_999": 10.0})


# ===========================================================================
# (4) DB read layer — fail-closed tenant isolation + kEUR assembly
# ===========================================================================
def _install_service_fakes(monkeypatch):
    """Fake the reused cash + OPOS builders at the liquidity boundary and RECORD
    the entity scope each receives (the security-visible seam)."""
    import app.services.liquidity as liq

    seen: dict[str, object] = {"ar_entities": [], "ap_entities": []}

    def _cash(session, *, year, month, ent_frag):
        seen["cash_frag"] = ent_frag
        return {"level": 120_000.0, "delta_month": 0.0, "delta_yoy": 0.0}  # EUR → 120 kEUR

    def _ar(session, year, month, entity=None):
        seen["ar_entities"].append(entity)
        return {"series": [
            {"band": "not_yet_due", "label": "Not yet due", "amount": 400.0},
            {"band": "overdue_1_30", "label": "1–30", "amount": 100.0},
            {"band": "overdue_31_60", "label": "31–60", "amount": 50.0},
            {"band": "overdue_61_90", "label": "61–90", "amount": 40.0},
            {"band": "overdue_91_180", "label": "91–180", "amount": 20.0},
            {"band": "overdue_over_180", "label": ">180", "amount": 10.0},
        ]}

    def _ap(session, year, month, entity=None):
        seen["ap_entities"].append(entity)
        return {"total_payables": 300.0}

    monkeypatch.setattr(liq, "_cash_headline", _cash)
    monkeypatch.setattr(liq, "build_receivables_aging_opos", _ar)
    monkeypatch.setattr(liq, "build_payables_aging_opos", _ap)
    return seen


def _prefix_session(prefix_to_code: dict[str, str] | None = None) -> MagicMock:
    """Session double: dim_legal_entity prefix→code lookups only (cash + OPOS are
    faked at the boundary, so no other SQL is issued)."""
    prefix_to_code = prefix_to_code or {}
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        rows = []
        if "dim_legal_entity" in sql and params and "prefixes" in params:
            rows = [_DictRow({"legal_entity_code": prefix_to_code[p], "entity_prefix": p})
                    for p in params["prefixes"] if p in prefix_to_code]
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


class TestBuildLiquidityAvailable:

    def test_admin_worked_example_405_keur(self, monkeypatch):
        seen = _install_service_fakes(monkeypatch)
        session = _prefix_session()
        out = build_liquidity_available(
            session, entity=None, year=2026, month=3, allowed_entities=None,
        )
        assert out["meta"]["unit"] == "kEUR"
        assert out["meta"]["visibility"] == "admin"
        assert out["cash"] == 120.0            # 120_000 EUR / 1000
        assert out["raw_ar"] == 620.0
        assert out["collectible_ar"] == 585.0
        assert out["outstanding_ap"] == 300.0
        assert out["liquidity_available"] == 405.0
        # admin path calls the OPOS builders exactly once with the (None) entity.
        assert seen["ar_entities"] == [None] and seen["ap_entities"] == [None]

    def test_fail_closed_empty_visibility_zeroed_and_no_subquery(self, monkeypatch):
        seen = _install_service_fakes(monkeypatch)
        session = _prefix_session()
        out = build_liquidity_available(
            session, entity=None, year=2026, month=3, allowed_entities=set(),
        )
        assert out["meta"]["source"] == "fail_closed"
        assert out["cash"] == 0.0
        assert out["collectible_ar"] == 0.0
        assert out["outstanding_ap"] == 0.0
        assert out["liquidity_available"] == 0.0
        # deny-all short-circuits BEFORE any cash/OPOS sub-query
        assert seen["ar_entities"] == [] and seen["ap_entities"] == []
        assert "cash_frag" not in seen

    def test_restricted_prefix_set_restricts_scope(self, monkeypatch):
        seen = _install_service_fakes(monkeypatch)
        session = _prefix_session(prefix_to_code={"10": "DE"})
        out = build_liquidity_available(
            session, entity=None, year=2026, month=3, allowed_entities={"10"},
        )
        assert out["meta"]["visibility"] == "restricted"
        # prefix '10' → representative code 'DE' scopes BOTH OPOS builders.
        assert seen["ar_entities"] == ["DE"] and seen["ap_entities"] == ["DE"]
        # cash query filtered to the visible prefix (not admin/no-filter).
        assert seen["cash_frag"] == "AND l.entity_prefix = '10'"
        assert out["liquidity_available"] == 405.0

    def test_all_zero_ar(self, monkeypatch):
        import app.services.liquidity as liq
        _install_service_fakes(monkeypatch)
        monkeypatch.setattr(
            liq, "build_receivables_aging_opos",
            lambda s, y, m, e=None: {"series": []},
        )
        monkeypatch.setattr(
            liq, "build_payables_aging_opos",
            lambda s, y, m, e=None: {"total_payables": 0.0},
        )
        session = _prefix_session()
        out = build_liquidity_available(
            session, entity=None, year=2026, month=3, allowed_entities=None,
        )
        assert out["raw_ar"] == 0.0 and out["collectible_ar"] == 0.0
        assert out["liquidity_available"] == 120.0  # cash only


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
