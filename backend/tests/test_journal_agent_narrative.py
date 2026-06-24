"""Journal Agent (Phase 6) — flag-gated GL-finding integration into the PL narrative.

GOLDEN-CRITICAL property pinned here:
  * with ``journal_agent_narrative`` OFF (the default) the PL narrative bullets are
    BYTE-IDENTICAL to the legacy output AND ``detect_gl_findings`` is never even
    called (literal no-op path);
  * with the flag ON (``detect_gl_findings`` monkeypatched to return >= 1 finding)
    at least one finding-bullet is appended AND the total bullet count stays <= cap.

Plus DB-free unit tests for:
  * ``detect_gl_findings`` ordering/severity (the 3 GL services monkeypatched);
  * ``merge_finding_bullets`` re-index + cap clamp;
  * the snapshot cache key namespacing by the flag.

No DB, no network — the statement / line-detail builders and the 3 GL services are
all monkeypatched, mirroring ``test_compat_narrative_core`` / ``test_anomaly``.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from app.services import anomaly
from app.services import fin_compat_narrative_core as core


# ---------------------------------------------------------------------------
# Statement / line-detail fixtures (same shape the compat builders emit)
# ---------------------------------------------------------------------------
def _line(
    code: str, label: str, cm: float, pm: float, py_cm: float = 0.0,
    *, ytd: float = 0.0, ytd_py: float = 0.0, mom: Optional[float] = None,
    yoy: Optional[float] = None, children: Optional[list] = None,
    row_kind: str = "line", plan_cm: Optional[float] = None,
) -> dict[str, Any]:
    amounts = {"py_cm": py_cm, "pm": pm, "cm": cm, "ytd": ytd, "ytd_py": ytd_py}
    if plan_cm is not None:
        amounts["plan_cm"] = plan_cm
        amounts["plan_vs_actual"] = round(cm - plan_cm, 2)
    return {
        "line_code": code, "label": label, "row_kind": row_kind, "kpi_code": "",
        "invert_delta": False, "amounts": amounts,
        "deltas": {
            "mom": (cm - pm) if mom is None else mom,
            "yoy": (cm - py_cm) if yoy is None else yoy,
            "ytd": ytd - ytd_py,
        },
        "children": children or [], "accounts": [],
    }


def _pl_statement() -> dict[str, Any]:
    return {
        "rows": [
            _line("NET_SALES", "Net sales", 520_000, 500_000, 470_000,
                  ytd=1_500_000, ytd_py=1_400_000),
            _line("MAT", "Materials", 300_000, 250_000, 260_000, mom=-50_000, yoy=-40_000),
            _line("NET_PROFIT", "Net profit", 130_000, 120_000, 110_000,
                  ytd=400_000, ytd_py=360_000, row_kind="subtotal", plan_cm=125_000),
        ],
        "col_labels": {"cm": "Jun25", "pm": "May25", "py_cm": "Jun24",
                       "ytd": "YTDJun25", "ytd_py": "YTDJun24"},
    }


def _fake_pl_line_detail(session, line_code, year, month, entity, **kw):
    if line_code == "MAT":
        return {
            "accounts": [{"account_name": "Raw steel", "gl_account_id": "400100",
                          "balance_cm": 300, "balance_pm": 250, "delta": 50}],
            "top_bookings": [{"amount": 40, "line_note": "Steel delivery 7788"}],
        }
    return {"accounts": [], "top_bookings": []}


def _patch_pl(monkeypatch) -> None:
    from app.services import fin_compat_narrative as narr

    monkeypatch.setattr(narr, "build_pl_statement_compat", lambda *a, **k: _pl_statement())
    monkeypatch.setattr(
        "app.services.fin_compat_line_detail.build_pl_line_detail", _fake_pl_line_detail
    )


# ===========================================================================
# GOLDEN: flag OFF → byte-identical bullets AND detect never called
# ===========================================================================
def test_flag_off_is_a_literal_noop(monkeypatch):
    from app.config import settings
    from app.services import fin_compat_narrative as narr

    monkeypatch.setattr(settings, "journal_agent_narrative", False, raising=False)
    _patch_pl(monkeypatch)

    # If the flag path were entered, this would raise — proving OFF is a no-op.
    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("detect_gl_findings must NOT be called when flag is OFF")

    monkeypatch.setattr(anomaly, "detect_gl_findings", _boom)
    monkeypatch.setattr(anomaly, "gl_findings_as_bullets", _boom)

    out = narr.build_pl_narrative(MagicMock(), 2025, 6, None)
    bullets = out["bullets"]
    assert [b["line_code"] for b in bullets][:2] == ["NET_SALES", "MAT"]
    # no finding-kind leaked into facts
    assert all("finding_kind" not in (b.get("facts") or {}) for b in bullets)


def test_flag_off_bullets_byte_identical_to_frozen(monkeypatch):
    """The OFF bullets must equal an independent rebuild (frozen golden)."""
    from app.config import settings
    from app.services import fin_compat_narrative as narr

    monkeypatch.setattr(settings, "journal_agent_narrative", False, raising=False)
    _patch_pl(monkeypatch)

    a = narr.build_pl_narrative(MagicMock(), 2025, 6, None)["bullets"]
    b = narr.build_pl_narrative(MagicMock(), 2025, 6, None)["bullets"]
    assert a == b  # deterministic
    # frozen expectation: exactly the two material drivers, no findings appended
    assert [x["line_code"] for x in a] == ["NET_SALES", "MAT"]
    assert "account 400100 *Raw steel*" in next(
        x for x in a if x["line_code"] == "MAT"
    )["text"]


# ===========================================================================
# Flag ON → >= 1 finding-bullet appended, total <= cap
# ===========================================================================
def test_flag_on_appends_finding_bullets_within_cap(monkeypatch):
    from app.config import settings
    from app.services import fin_compat_narrative as narr

    monkeypatch.setattr(settings, "journal_agent_narrative", True, raising=False)
    _patch_pl(monkeypatch)

    # Two PL findings + one BS finding (BS must be filtered out of the PL narrative).
    findings = [
        anomaly.Anomaly(
            statement="pl", line_code="700000", label="Repairs",
            kind="outlier", severity="high", period_grain="month",
            value=400_000, delta=350_000, magnitude_eur=350_000,
            description="account 700000 *Repairs* shows an outlier month.",
        ),
        anomaly.Anomaly(
            statement="pl", line_code="650000", label="Other expenses",
            kind="other_position", severity="medium", period_grain="month",
            value=120_000, delta=100_000, magnitude_eur=100_000,
            description='account 650000 *Other expenses* ("Other") is growing.',
        ),
        anomaly.Anomaly(
            statement="bs", line_code="160000", label="Trade payables",
            kind="counter_account", severity="high", period_grain="month",
            value=500_000, delta=500_000, magnitude_eur=500_000,
            description="BS finding that must NOT appear in the PL narrative.",
        ),
    ]
    monkeypatch.setattr(anomaly, "detect_gl_findings", lambda *a, **k: findings)

    out = narr.build_pl_narrative(MagicMock(), 2025, 6, None)
    bullets = out["bullets"]

    cap = core.clamp_cap(None)
    assert len(bullets) <= cap

    finding_codes = {b["line_code"] for b in bullets
                     if "finding_kind" in (b.get("facts") or {})}
    assert finding_codes, "at least one finding-bullet must be appended when ON"
    assert "160000" not in finding_codes  # BS finding filtered out of PL

    # bullets re-indexed 0..n-1 after the merge
    assert [b["index"] for b in bullets] == list(range(len(bullets)))
    # every finding-bullet keeps the canonical bullet shape
    for b in bullets:
        assert set(b) == {"index", "line_code", "label", "text", "tone",
                          "deep_links", "facts"}


def test_flag_on_with_no_findings_is_identical_to_off(monkeypatch):
    """ON but zero findings → bullets identical to the OFF path (no empty padding)."""
    from app.config import settings
    from app.services import fin_compat_narrative as narr

    _patch_pl(monkeypatch)
    monkeypatch.setattr(settings, "journal_agent_narrative", False, raising=False)
    off = narr.build_pl_narrative(MagicMock(), 2025, 6, None)["bullets"]

    monkeypatch.setattr(settings, "journal_agent_narrative", True, raising=False)
    monkeypatch.setattr(anomaly, "detect_gl_findings", lambda *a, **k: [])
    on = narr.build_pl_narrative(MagicMock(), 2025, 6, None)["bullets"]
    assert on == off


# ===========================================================================
# detect_gl_findings — ordering / severity (the 3 services monkeypatched)
# ===========================================================================
def test_detect_gl_findings_orders_by_severity_then_magnitude(monkeypatch):
    """A big outlier (high), a medium seasonality, and a small counter (low) must
    come back ordered high → medium → low by the existing ``sort_key``."""
    out_payload = {
        "accounts": [{
            "gl_account_id": "700000", "account_name": "Repairs", "statement": "pl",
            "series": [
                {"label": "Jun24", "value_keur": 50, "residual_keur": 0, "z": 0.1},
                {"label": "Jun25", "value_keur": 400, "residual_keur": 350, "z": 2.5},
            ],
        }],
    }
    sea_payload = {
        "accounts": [{
            "gl_account_id": "650000", "account_name": "Energy", "statement": "pl",
            "insufficient_history": False,
            "series": [
                {"label": "Dec24", "actual_keur": 120, "residual_keur": 100, "z": 1.8},
                {"label": "Nov24", "actual_keur": 20, "residual_keur": 5, "z": 0.2},
            ],
        }],
    }
    fore_payload = {
        "unexpected_counter_accounts": [{
            "gl_account_id": "800000", "account_name": "Misc",
            "counter_gl_account_id": "999", "counter_account_name": "Suspense",
            "amount_keur": 40, "freq_pct": 0.01, "novelty": "rare",
        }],
        "other_positions": [],
        "suspicious_texts": [],
    }
    monkeypatch.setattr(
        "app.services.gl_outliers.build_outliers", lambda *a, **k: out_payload
    )
    monkeypatch.setattr(
        "app.services.gl_seasonality.build_seasonality", lambda *a, **k: sea_payload
    )
    monkeypatch.setattr(
        "app.services.gl_forensic.build_forensic", lambda *a, **k: fore_payload
    )

    period = {"grain": "month", "year": 2025, "month": 6,
              "iso_year": None, "iso_week": None}
    findings = anomaly.detect_gl_findings(MagicMock(), period, None)

    kinds = [f.kind for f in findings]
    assert set(kinds) == {"outlier", "seasonality", "counter_account"}
    # 350k → high; 100k → medium; 40k → low.
    assert findings[0].kind == "outlier" and findings[0].severity == "high"
    assert findings[-1].kind == "counter_account" and findings[-1].severity == "low"
    # magnitude descending within the high→medium→low banding
    mags = [f.magnitude_eur for f in findings]
    assert mags == sorted(mags, reverse=True)
    # period stamped
    assert all(f.year == 2025 and f.month == 6 for f in findings)


def test_detect_gl_findings_skips_insufficient_and_subthreshold(monkeypatch):
    """Insufficient-history seasonality accounts and |z| < σ outliers are dropped."""
    out_payload = {
        "accounts": [{
            "gl_account_id": "1", "account_name": "Flat", "statement": "pl",
            "series": [{"label": "Jun25", "value_keur": 10, "residual_keur": 1, "z": 0.3}],
        }],
    }
    sea_payload = {
        "accounts": [{
            "gl_account_id": "2", "account_name": "Young", "statement": "pl",
            "insufficient_history": True,
            "series": [{"label": "Jun25", "actual_keur": 10, "residual_keur": 9, "z": 3.0}],
        }],
    }
    monkeypatch.setattr("app.services.gl_outliers.build_outliers", lambda *a, **k: out_payload)
    monkeypatch.setattr("app.services.gl_seasonality.build_seasonality", lambda *a, **k: sea_payload)
    monkeypatch.setattr(
        "app.services.gl_forensic.build_forensic",
        lambda *a, **k: {"unexpected_counter_accounts": [], "other_positions": [],
                         "suspicious_texts": []},
    )
    period = {"grain": "month", "year": 2025, "month": 6,
              "iso_year": None, "iso_week": None}
    findings = anomaly.detect_gl_findings(MagicMock(), period, None)
    assert findings == []


def test_gl_findings_as_bullets_filters_by_statement(monkeypatch):
    findings = [
        anomaly.Anomaly("pl", "A", "A", "outlier", "high", "month",
                        1, 1, 100_000, "pl one"),
        anomaly.Anomaly("bs", "B", "B", "outlier", "high", "month",
                        1, 1, 100_000, "bs one"),
    ]
    monkeypatch.setattr(anomaly, "detect_gl_findings", lambda *a, **k: findings)
    bullets = anomaly.gl_findings_as_bullets(
        MagicMock(), {"grain": "month", "year": 2025, "month": 6}, None, "pl"
    )
    assert [b["line_code"] for b in bullets] == ["A"]
    assert bullets[0]["facts"]["finding_kind"] == "outlier"


# ===========================================================================
# merge_finding_bullets — re-index + cap clamp
# ===========================================================================
def _bullet(code: str) -> dict[str, Any]:
    return {"index": 0, "line_code": code, "label": code, "text": code,
            "tone": "neutral", "deep_links": [], "facts": {}}


def test_merge_finding_bullets_appends_reindexes_and_clamps():
    base = [_bullet(f"D{i}") for i in range(4)]
    extra = [_bullet(f"F{i}") for i in range(3)]
    merged = core.merge_finding_bullets(base, extra, cap=5)
    # cap=5 → 4 drivers + 1 finding
    assert [b["line_code"] for b in merged] == ["D0", "D1", "D2", "D3", "F0"]
    assert [b["index"] for b in merged] == [0, 1, 2, 3, 4]


def test_merge_finding_bullets_empty_extra_is_noop_within_cap():
    base = [_bullet(f"D{i}") for i in range(3)]
    merged = core.merge_finding_bullets(base, [], cap=5)
    assert [b["line_code"] for b in merged] == ["D0", "D1", "D2"]
    assert [b["index"] for b in merged] == [0, 1, 2]


# ===========================================================================
# Snapshot cache key — namespaced by the flag (C3)
# ===========================================================================
def test_entity_scope_key_unchanged_when_flag_off(monkeypatch):
    from app.config import settings
    from app.services import fin_compat_narrative_snapshot_cache as cache

    monkeypatch.setattr(settings, "journal_agent_narrative", False, raising=False)
    assert cache.entity_scope_key(None) == ""
    assert cache.entity_scope_key("all") == ""
    assert cache.entity_scope_key("DE01") == "DE01"


def test_entity_scope_key_namespaced_when_flag_on(monkeypatch):
    from app.config import settings
    from app.services import fin_compat_narrative_snapshot_cache as cache

    monkeypatch.setattr(settings, "journal_agent_narrative", True, raising=False)
    assert cache.entity_scope_key(None) == "|ja"
    assert cache.entity_scope_key("DE01") == "DE01|ja"
    # ON key must differ from the OFF key so an OFF-warmed snapshot is never served.
    monkeypatch.setattr(settings, "journal_agent_narrative", False, raising=False)
    assert cache.entity_scope_key("DE01") == "DE01"
