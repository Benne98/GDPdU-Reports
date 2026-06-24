"""Tests for anomaly_score (anomaly rework v2, Phase 0) — laymen signal score.

PURE / DB-FREE.  Locks the 0..100 ``signal_score_0to100`` mapping (monotonicity,
worked-example anchor points, saturation, sign-agnosticism, defensive non-finite
handling) and the ``band`` boundaries (33 / 66).  This is presentation logic, NOT a
financial KPI — but CLAUDE.md rule #1 still applies (formula + example + edge + test).
"""
from __future__ import annotations

import math

import pytest

from app.services.anomaly_score import (
    SCORE_BAND_HIGH,
    SCORE_BAND_LOW,
    band,
    signal_score_0to100,
)


# --------------------------------------------------------------------------- #
# signal_score_0to100 — worked-example anchor points
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "z, expected",
    [
        (0.0, 0),     # on the mean → normal
        (1.0, 33),    # exactly 1σ → band edge
        (1.5, 50),    # round(33 + 0.5*33) = round(49.5) = 50 (banker's)
        (2.0, 66),    # exactly 2σ → band edge
        (2.5, 82),    # round(66 + 0.5*33) = round(82.5) = 82 (banker's rounding)
        (3.0, 100),   # >= 3σ → saturate
        (5.0, 100),   # saturate
    ],
)
def test_signal_score_worked_examples(z, expected):
    assert signal_score_0to100(z) == expected


def test_signal_score_is_sign_agnostic():
    # |z| is used → negative z gives the same score as its positive twin.
    for z in (0.5, 1.0, 1.7, 2.3, 3.0, 7.0):
        assert signal_score_0to100(-z) == signal_score_0to100(z)


def test_signal_score_monotonic_nondecreasing():
    prev = -1
    z = 0.0
    while z <= 6.0:
        s = signal_score_0to100(z)
        assert s >= prev, f"score dropped at z={z}: {s} < {prev}"
        prev = s
        z += 0.05


def test_signal_score_range_bounds():
    for z in (0.0, 0.3, 1.1, 2.9, 3.0, 100.0):
        s = signal_score_0to100(z)
        assert 0 <= s <= 100


def test_signal_score_saturates_at_100():
    assert signal_score_0to100(3.0) == 100
    assert signal_score_0to100(3.0001) == 100
    assert signal_score_0to100(1e9) == 100


def test_signal_score_just_below_band_edge_stays_lower():
    # Within a band the score rounds, so it reaches the next band edge a hair
    # before the integer σ boundary (round(0.99*33)=33).  Use values comfortably
    # inside the band to assert it stays below the edge.
    assert signal_score_0to100(0.90) < 33   # round(29.7) = 30
    assert signal_score_0to100(1.90) < 66   # round(33 + 0.9*33) = round(62.7) = 63


# --------------------------------------------------------------------------- #
# signal_score_0to100 — edge / defensive
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_signal_score_nonfinite_is_zero(bad):
    assert signal_score_0to100(bad) == 0


def test_signal_score_none_is_zero():
    # Defensive: callers pass finite z, but a stray None must not raise.
    assert signal_score_0to100(None) == 0  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# band boundaries
# --------------------------------------------------------------------------- #
def test_band_boundaries():
    assert band(0) == "low"
    assert band(32) == "low"
    assert band(SCORE_BAND_LOW) == "medium"   # 33 → medium (inclusive lower edge)
    assert band(65) == "medium"
    assert band(SCORE_BAND_HIGH) == "high"     # 66 → high (inclusive lower edge)
    assert band(100) == "high"


def test_band_constants_align_with_score_edges():
    # The score edges ARE the |z|=1 and |z|=2 crossings, so band(score(z)) agrees.
    assert band(signal_score_0to100(1.0)) == "medium"   # score 33
    assert band(signal_score_0to100(0.5)) == "low"      # score ~16
    assert band(signal_score_0to100(2.0)) == "high"     # score 66
    assert band(signal_score_0to100(3.0)) == "high"     # score 100
