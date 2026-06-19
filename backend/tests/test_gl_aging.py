"""Pure tests for GL AR/AP aging bucket logic (no DB)."""
from __future__ import annotations

from app.services.gl_aging import AR_BANDS, bucket_ar_amounts


def test_bucket_ar_amounts_empty():
    series = bucket_ar_amounts([])
    assert len(series) == len(AR_BANDS)
    assert all(s["amount"] == 0.0 for s in series)
    assert series[0]["band"] == "not_yet_due"
    assert series[0]["label"] == "Not yet due"


def test_bucket_ar_amounts_aggregates_and_orders():
    rows = [
        ("overdue_1_30", 50.0),
        ("not_yet_due", 100.0),
        ("overdue_1_30", 25.5),
        ("overdue_over_180", 10.0),
    ]
    series = bucket_ar_amounts(rows)
    assert [s["band"] for s in series] == [b for b, _ in AR_BANDS]
    assert series[0]["amount"] == 100.0
    assert series[1]["amount"] == 75.5
    assert series[-1]["amount"] == 10.0


def test_bucket_ar_amounts_ignores_unknown_bands():
    series = bucket_ar_amounts([("unknown_band", 999.0), ("not_yet_due", 12.0)])
    assert series[0]["amount"] == 12.0
    assert sum(s["amount"] for s in series) == 12.0
