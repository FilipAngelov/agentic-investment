"""Unit tests for data/indicators.py."""

import math

import pytest

from data.indicators import (
    atr,
    ema,
    historical_volatility,
    macd,
    rate_of_change,
    relative_volume,
    rsi,
    sma,
)


# ---------------------------------------------------------------------------
# SMA
# ---------------------------------------------------------------------------


class TestSMA:
    def test_basic(self):
        result = sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
        assert result[:2] == [None, None]
        assert result[2] == pytest.approx(2.0)
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(4.0)

    def test_period_equals_length(self):
        result = sma([10.0, 20.0, 30.0], 3)
        assert result == [None, None, pytest.approx(20.0)]

    def test_period_greater_than_length(self):
        result = sma([1.0, 2.0], 5)
        assert result == [None, None]

    def test_empty(self):
        assert sma([], 3) == []

    def test_period_1(self):
        vals = [5.0, 10.0, 15.0]
        result = sma(vals, 1)
        assert result == [pytest.approx(5.0), pytest.approx(10.0), pytest.approx(15.0)]


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------


class TestEMA:
    def test_seed_is_sma(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = ema(vals, 3)
        # Seed at index 2 = SMA(1,2,3) = 2.0
        assert result[2] == pytest.approx(2.0)

    def test_smoothing(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = ema(vals, 3)
        k = 2.0 / 4.0  # 0.5
        expected_3 = 4.0 * k + 2.0 * (1 - k)  # 3.0
        assert result[3] == pytest.approx(expected_3)

    def test_period_greater_than_length(self):
        result = ema([1.0], 5)
        assert result == [None]


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


class TestRSI:
    def test_trending_up(self):
        # Monotonically increasing prices → high RSI
        closes = [float(i) for i in range(30)]
        result = rsi(closes, 14)
        assert result[-1] is not None
        assert result[-1] > 80

    def test_trending_down(self):
        closes = [float(30 - i) for i in range(30)]
        result = rsi(closes, 14)
        assert result[-1] is not None
        assert result[-1] < 20

    def test_flat(self):
        closes = [50.0] * 30
        result = rsi(closes, 14)
        # No movement → gains and losses are 0, result should be None or edge case
        # With all zeros, avg_loss=0 → RSI=100 for first, then stays 100
        assert result[14] == 100.0

    def test_too_short(self):
        result = rsi([1.0, 2.0], 14)
        assert all(v is None for v in result)

    def test_length_preserved(self):
        closes = [float(i) for i in range(50)]
        result = rsi(closes, 14)
        assert len(result) == 50


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------


class TestMACD:
    def test_lengths(self):
        closes = [float(i) for i in range(60)]
        ml, sl, hist = macd(closes)
        assert len(ml) == 60
        assert len(sl) == 60
        assert len(hist) == 60

    def test_macd_line_starts_at_slow(self):
        closes = [float(100 + i) for i in range(60)]
        ml, _, _ = macd(closes)
        # MACD line should be None before slow EMA is ready (index 25)
        assert ml[24] is None
        assert ml[25] is not None

    def test_histogram_sign(self):
        # Strongly trending up: fast EMA > slow EMA → positive MACD
        closes = [float(i) for i in range(60)]
        ml, sl, hist = macd(closes)
        last_ml = [v for v in ml if v is not None][-1]
        assert last_ml > 0  # fast responds quicker to uptrend


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------


class TestATR:
    def test_basic(self):
        n = 20
        highs = [float(102 + i % 3) for i in range(n)]
        lows = [float(98 - i % 3) for i in range(n)]
        closes = [float(100) for _ in range(n)]
        result = atr(highs, lows, closes, period=5)
        assert len(result) == n
        # Should have values from index 5 onward
        assert result[4] is None
        assert result[5] is not None
        assert result[5] > 0

    def test_too_short(self):
        result = atr([10.0], [9.0], [9.5], period=5)
        assert result == [None]

    def test_true_range_uses_prev_close(self):
        # Gap up scenario: high-low is small but |high - prev_close| is large
        highs = [10.0, 10.0, 20.0]
        lows = [9.0, 9.0, 19.0]
        closes = [9.5, 9.5, 19.5]
        result = atr(highs, lows, closes, period=1)
        # TR at index 2: max(20-19, |20-9.5|, |19-9.5|) = 10.5
        assert result[1] is not None
        assert result[2] == pytest.approx(
            (result[1] * 0 + 10.5) / 1  # Wilder with period=1
        )


# ---------------------------------------------------------------------------
# Rate of Change
# ---------------------------------------------------------------------------


class TestROC:
    def test_basic(self):
        vals = [100.0, 110.0, 120.0, 130.0]
        result = rate_of_change(vals, 2)
        assert result[0] is None
        assert result[1] is None
        assert result[2] == pytest.approx(20.0)  # (120-100)/100*100
        assert result[3] == pytest.approx((130 - 110) / 110 * 100)

    def test_zero_base(self):
        result = rate_of_change([0.0, 5.0], 1)
        assert result[1] is None


# ---------------------------------------------------------------------------
# Historical Volatility
# ---------------------------------------------------------------------------


class TestHV:
    def test_constant_prices(self):
        closes = [100.0] * 30
        result = historical_volatility(closes, 20)
        # Constant prices → zero volatility
        assert result[-1] == pytest.approx(0.0)

    def test_returns_annualized(self):
        # Just check it returns something positive for varying prices
        closes = [100.0 + i * 0.5 for i in range(30)]
        result = historical_volatility(closes, 20)
        assert result[-1] is not None
        assert result[-1] > 0


# ---------------------------------------------------------------------------
# Relative Volume
# ---------------------------------------------------------------------------


class TestRelativeVolume:
    def test_basic(self):
        # SMA includes current bar, so avg at index 21 = (19*100 + 200)/20 = 105
        vols = [100] * 21 + [200]
        result = relative_volume(vols, 20)
        assert result[-1] == pytest.approx(200.0 / 105.0)

    def test_warmup(self):
        vols = [100] * 10
        result = relative_volume(vols, 20)
        assert all(v is None for v in result)
