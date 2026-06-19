"""FY-end session cache fallback logic (mirrors actions.py; keep in sync)."""
from __future__ import annotations

import unittest

_SESSION_FY_END: dict[str, tuple[int, int]] = {}


def _remember_session_fy_end(sender_id: str, month: int, day: int) -> None:
    m = max(1, min(12, int(month)))
    d = max(1, min(31, int(day)))
    _SESSION_FY_END[sender_id] = (m, d)


def _cached_session_fy_end(sender_id: str) -> tuple[int, int] | None:
    return _SESSION_FY_END.get(sender_id)


def _fy_end_month_day_from_slots(
    raw_m,
    raw_d,
    sender_id: str,
) -> tuple[int, int]:
    cached = _cached_session_fy_end(sender_id)
    if raw_m in (None, "") and cached is not None:
        raw_m = cached[0]
    if raw_d in (None, "") and cached is not None:
        raw_d = cached[1]
    try:
        m = int(float(raw_m)) if raw_m not in (None, "") else 12
    except (ValueError, TypeError):
        m = 12
    try:
        d = int(float(raw_d)) if raw_d not in (None, "") else 31
    except (ValueError, TypeError):
        d = 31
    return max(1, min(12, m)), max(1, min(31, d))


class TestFyEndSessionCache(unittest.TestCase):
    def setUp(self):
        _SESSION_FY_END.clear()

    def test_slots_take_priority_over_cache(self):
        _remember_session_fy_end("s1", 12, 31)
        m, d = _fy_end_month_day_from_slots("7", 31, "s1")
        self.assertEqual((m, d), (7, 31))

    def test_cache_used_when_slots_empty(self):
        _remember_session_fy_end("s1", 7, 31)
        m, d = _fy_end_month_day_from_slots(None, None, "s1")
        self.assertEqual((m, d), (7, 31))

    def test_defaults_without_cache(self):
        m, d = _fy_end_month_day_from_slots(None, None, "s1")
        self.assertEqual((m, d), (12, 31))


if __name__ == "__main__":
    unittest.main()
