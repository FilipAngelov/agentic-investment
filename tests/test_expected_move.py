"""Tests for signals/expected_move.py — Expected Move calculator."""

from __future__ import annotations

import math

import pytest

from data.models import Bar
from signals.expected_move import (
    ExpectedMove,
    calculate_expected_move,
    compute_beta,
    compute_expected_move,
    css_to_multiplier,
)


# ---------------------------------------------------------------------------
# css_to_multiplier
# ---------------------------------------------------------------------------


class TestCssToMultiplier:
    def test_anchors(self):
        assert css_to_multiplier(1.0) == pytest.approx(1.0)
        assert css_to_multiplier(3.0) == pytest.approx(2.0)
        assert css_to_multiplier(5.0) == pytest.approx(5.0)

    def test_interpolation(self):
        assert css_to_multiplier(2.0) == pytest.approx(1.5)
        assert css_to_multiplier(4.0) == pytest.approx(3.0)

    def test_clamp_low(self):
        assert css_to_multiplier(0.0) == pytest.approx(1.0)
        assert css_to_multiplier(0.5) == pytest.approx(1.0)

    def test_clamp_high(self):
        assert css_to_multiplier(10.0) == pytest.approx(5.0)

    def test_negative_uses_abs(self):
        assert css_to_multiplier(-3.0) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# compute_beta
# ---------------------------------------------------------------------------


class TestComputeBeta:
    def test_perfect_correlation(self):
        # Build prices from returns: stock return = 2× bench return
        bench = [100.0]
        stock = [100.0]
        for i in range(24):
            r = 0.01 * (1 if i % 2 == 0 else -1)  # alternating 1% moves
            bench.append(bench[-1] * (1 + r))
            stock.append(stock[-1] * (1 + 2 * r))
        beta = compute_beta(stock, bench, period=20)
        assert beta is not None
        assert beta == pytest.approx(2.0, abs=0.05)

    def test_insufficient_data(self):
        assert compute_beta([1.0, 2.0], [1.0, 2.0], period=20) is None


# ---------------------------------------------------------------------------
# compute_expected_move
# ---------------------------------------------------------------------------


class TestComputeExpectedMove:
    def test_basic(self):
        hv20 = 0.30  # 30% annualized
        daily_vol = hv20 / math.sqrt(252)
        em = compute_expected_move(css=3.0, beta=1.5, hv20=hv20, regime_factor=1.0)
        expected = css_to_multiplier(3.0) * 1.5 * daily_vol * 1.0
        assert em == pytest.approx(expected)


# ---------------------------------------------------------------------------
# calculate_expected_move (orchestrator)
# ---------------------------------------------------------------------------


def _make_bar(close: float, i: int = 0) -> Bar:
    return Bar(
        symbol="TEST",
        timestamp=i,
        timeframe="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
    )


class TestCalculateExpectedMove:
    def test_orchestration(self):
        # 30 bars, stock drifts up, benchmark flat-ish
        stock_bars = [_make_bar(100.0 + i * 0.5, i) for i in range(30)]
        bench_bars = [_make_bar(400.0 + i * 0.25, i) for i in range(30)]
        result = calculate_expected_move(
            stock_bars, bench_bars, css=2.0, regime="bull", direction="LONG"
        )
        assert result is not None
        assert isinstance(result, ExpectedMove)
        assert result.em_pct > 0
        assert result.css_multiplier == pytest.approx(1.5)
        assert result.regime_factor == 1.0

    def test_insufficient_data(self):
        bars = [_make_bar(100.0, i) for i in range(5)]
        result = calculate_expected_move(
            bars, bars, css=2.0, regime="bull", direction="LONG"
        )
        assert result is None

    def test_bear_regime_long_uses_lower_factor(self):
        stock_bars = [_make_bar(100.0 + i * 0.5, i) for i in range(30)]
        bench_bars = [_make_bar(400.0 + i * 0.25, i) for i in range(30)]
        result = calculate_expected_move(
            stock_bars, bench_bars, css=2.0, regime="bear", direction="LONG"
        )
        assert result is not None
        assert result.regime_factor == 0.5

    def test_bear_regime_short_uses_default_factor(self):
        stock_bars = [_make_bar(100.0 + i * 0.5, i) for i in range(30)]
        bench_bars = [_make_bar(400.0 + i * 0.25, i) for i in range(30)]
        result = calculate_expected_move(
            stock_bars, bench_bars, css=2.0, regime="bear", direction="SHORT"
        )
        assert result is not None
        assert result.regime_factor == 0.8
