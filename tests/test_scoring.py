"""Tests for signals/scoring.py — composite signal scoring."""

from __future__ import annotations

import time

import pytest

from data.models import Bar, Catalyst, Signal
from signals.expected_move import ExpectedMove
from signals.scoring import (
    MIN_COMPOSITE_SCORE,
    W_CATALYST,
    W_EM,
    W_SECTOR,
    W_TECHNICAL,
    normalize_catalyst,
    normalize_expected_move,
    normalize_sector,
    passes_score_threshold,
    score_candidate,
    score_signal,
)


def _make_signal(**overrides) -> Signal:
    defaults = dict(
        symbol="TEST",
        direction="LONG",
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        confidence=0.7,
        score=0.7,
        regime="bull",
        timestamp=int(time.time()),
    )
    defaults.update(overrides)
    return Signal(**defaults)


# ---------------------------------------------------------------------------
# normalize_catalyst
# ---------------------------------------------------------------------------


def test_normalize_catalyst_long_positive():
    assert normalize_catalyst(3.0, "LONG") == pytest.approx(0.6)


def test_normalize_catalyst_long_negative():
    assert normalize_catalyst(-3.0, "LONG") == 0.0


def test_normalize_catalyst_short_negative():
    assert normalize_catalyst(-3.0, "SHORT") == pytest.approx(0.6)


def test_normalize_catalyst_zero():
    assert normalize_catalyst(0.0, "LONG") == 0.5
    assert normalize_catalyst(0.0, "SHORT") == 0.5


# ---------------------------------------------------------------------------
# normalize_sector
# ---------------------------------------------------------------------------


def test_normalize_sector_all_strong():
    # momentum=0.08 → 0.08*10+0.5=1.3 → clamped 1.0
    # rs=1.8 → 1.8/2=0.9
    # accelerating=True → 1.0
    # 0.4*1.0 + 0.4*0.9 + 0.2*1.0 = 0.96
    result = normalize_sector(0.08, 1.8, True)
    assert result == pytest.approx(0.96)


def test_normalize_sector_none_defaults():
    # All None → 0.4*0.5 + 0.4*0.5 + 0.2*0.0 = 0.4
    result = normalize_sector(None, None, False)
    assert result == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# normalize_expected_move
# ---------------------------------------------------------------------------


def test_normalize_expected_move_high():
    assert normalize_expected_move(0.05) == pytest.approx(1.0)


def test_normalize_expected_move_none():
    assert normalize_expected_move(None) == 0.5


# ---------------------------------------------------------------------------
# score_signal
# ---------------------------------------------------------------------------


def test_score_signal_weights_sum():
    sig = _make_signal(confidence=0.8)
    em = ExpectedMove(em_pct=0.025, css=2.0, css_multiplier=1.5, beta=1.0, hv20=0.2, regime_factor=1.0)
    result = score_signal(sig, css=2.0, sector_momentum=0.05, sector_rs=1.0, sector_accelerating=True, em=em)

    tech = 0.8
    cat = 2.0 / 5.0  # 0.4
    sect = 0.4 * min(0.05 * 10 + 0.5, 1.0) + 0.4 * (1.0 / 2.0) + 0.2 * 1.0  # 0.4*1.0 + 0.4*0.5 + 0.2 = 0.8
    em_c = 0.025 / 0.05  # 0.5
    expected = W_TECHNICAL * tech + W_CATALYST * cat + W_SECTOR * sect + W_EM * em_c
    assert result.score == pytest.approx(expected, abs=1e-3)


def test_score_signal_preserves_confidence():
    sig = _make_signal(confidence=0.7)
    result = score_signal(sig, css=0.0, sector_momentum=None, sector_rs=None, sector_accelerating=False, em=None)
    assert result.confidence == 0.7


def test_score_signal_updates_score_and_em():
    sig = _make_signal(confidence=0.6)
    em = ExpectedMove(em_pct=0.03, css=1.0, css_multiplier=1.0, beta=1.0, hv20=0.2, regime_factor=1.0)
    result = score_signal(sig, css=1.0, sector_momentum=None, sector_rs=None, sector_accelerating=False, em=em)
    assert result.score != sig.score or result.expected_move_pct != sig.expected_move_pct
    assert result.expected_move_pct == pytest.approx(0.03)


# ---------------------------------------------------------------------------
# score_candidate (end-to-end)
# ---------------------------------------------------------------------------


def _make_bars(n: int, symbol: str = "TEST", base_price: float = 100.0) -> list[Bar]:
    """Generate n synthetic daily bars with slight uptrend."""
    bars = []
    ts = int(time.time()) - n * 86400
    for i in range(n):
        p = base_price + i * 0.1
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=ts + i * 86400,
                timeframe="1d",
                low=p - 1,
                high=p + 1,
                open=p - 0.5,
                close=p + 0.5,
                volume=1_000_000,
            )
        )
    return bars


def test_score_candidate_end_to_end():
    sig = _make_signal(confidence=0.6)
    stock_bars = _make_bars(50, "TEST")
    bench_bars = _make_bars(50, "SPY")
    catalysts = [
        Catalyst(timestamp=int(time.time()), headline="Good news", source="test", sentiment=0.8, magnitude=3),
    ]
    sector_info = {"momentum": 0.03, "relative_strength": 1.2, "accelerating": True}

    result = score_candidate(sig, stock_bars, bench_bars, catalysts, sector_info, "bull")
    assert 0.0 <= result.score <= 1.0
    assert result.confidence == 0.6  # preserved


# ---------------------------------------------------------------------------
# passes_score_threshold
# ---------------------------------------------------------------------------


def test_passes_score_threshold():
    above = _make_signal(score=MIN_COMPOSITE_SCORE + 0.01)
    below = _make_signal(score=MIN_COMPOSITE_SCORE - 0.01)
    assert passes_score_threshold(above) is True
    assert passes_score_threshold(below) is False
