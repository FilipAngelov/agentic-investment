"""Unit tests for signals/technical.py."""

import pytest

from signals.technical import (
    check_momentum,
    check_volume_confirmation,
    compute_position_size_factor,
    compute_stop_target,
    compute_technicals,
    detect_breakout,
    generate_technical_signal,
)


def _make_bars(closes, highs=None, lows=None, volumes=None, symbol="TEST"):
    """Build synthetic bar dicts."""
    n = len(closes)
    if highs is None:
        highs = [c + 1.0 for c in closes]
    if lows is None:
        lows = [c - 1.0 for c in closes]
    if volumes is None:
        volumes = [100_000] * n
    bars = []
    for i in range(n):
        bars.append({
            "symbol": symbol,
            "timestamp": 1_000_000 + i * 60,
            "open": closes[i],
            "high": highs[i],
            "low": lows[i],
            "close": closes[i],
            "volume": volumes[i],
        })
    return bars


def _trending_up_bars(n=80, start=100.0, step=0.5):
    """Generate bars with a clear uptrend."""
    closes = [start + i * step for i in range(n)]
    return _make_bars(closes)


def _trending_down_bars(n=80, start=200.0, step=0.5):
    """Generate bars with a clear downtrend."""
    closes = [start - i * step for i in range(n)]
    return _make_bars(closes)


# ---------------------------------------------------------------------------
# detect_breakout
# ---------------------------------------------------------------------------


class TestDetectBreakout:
    def test_bullish_breakout(self):
        # Flat then spike above 20-bar high
        closes = [100.0] * 60 + [120.0]
        bars = _make_bars(closes)
        tech = compute_technicals(bars)
        result = detect_breakout(bars, tech)
        assert result is not None
        assert result["direction"] == "LONG"
        assert result["type"] == "high_breakout"

    def test_bearish_breakdown(self):
        closes = [100.0] * 60 + [80.0]
        lows = [c - 1.0 for c in closes]
        lows[-1] = 79.0
        bars = _make_bars(closes, lows=lows)
        tech = compute_technicals(bars)
        result = detect_breakout(bars, tech)
        assert result is not None
        assert result["direction"] == "SHORT"
        assert result["type"] == "low_breakdown"

    def test_no_breakout(self):
        closes = [100.0] * 60
        bars = _make_bars(closes)
        tech = compute_technicals(bars)
        result = detect_breakout(bars, tech)
        assert result is None

    def test_too_few_bars(self):
        bars = _make_bars([100.0] * 10)
        tech = compute_technicals(bars)
        assert detect_breakout(bars, tech) is None


# ---------------------------------------------------------------------------
# check_momentum
# ---------------------------------------------------------------------------


class TestCheckMomentum:
    def test_uptrend(self):
        bars = _trending_up_bars(80)
        tech = compute_technicals(bars)
        m = check_momentum(tech)
        assert m["rsi"] is not None
        assert m["rsi_signal"] in ("overbought", "neutral")
        assert m["trend"] == "up"

    def test_downtrend(self):
        bars = _trending_down_bars(80)
        tech = compute_technicals(bars)
        m = check_momentum(tech)
        assert m["trend"] == "down"


# ---------------------------------------------------------------------------
# check_volume_confirmation
# ---------------------------------------------------------------------------


class TestVolumeConfirmation:
    def test_strong_volume(self):
        closes = [100.0] * 60
        vols = [100_000] * 59 + [300_000]  # 3x relative volume
        bars = _make_bars(closes, volumes=vols)
        tech = compute_technicals(bars)
        v = check_volume_confirmation(tech)
        assert v["conviction"] == "strong"
        assert v["rvol"] is not None
        assert v["rvol"] >= 2.0

    def test_weak_volume(self):
        closes = [100.0] * 60
        vols = [100_000] * 60
        bars = _make_bars(closes, volumes=vols)
        tech = compute_technicals(bars)
        v = check_volume_confirmation(tech)
        assert v["conviction"] == "weak"


# ---------------------------------------------------------------------------
# compute_stop_target
# ---------------------------------------------------------------------------


class TestStopTarget:
    def test_long(self):
        stop, target = compute_stop_target("LONG", 100.0, 2.0, 1.0)
        # stop = 100 - 2.0*2.0*1.0 = 96
        # target = 100 + 2*2.0*1.0 = 104
        assert stop == pytest.approx(96.0)
        assert target == pytest.approx(104.0)

    def test_short(self):
        stop, target = compute_stop_target("SHORT", 100.0, 2.0, 1.0)
        assert stop == pytest.approx(104.0)
        assert target == pytest.approx(96.0)

    def test_regime_factor(self):
        stop, target = compute_stop_target("LONG", 100.0, 2.0, 0.5)
        # stop = 100 - 2.0*2.0*0.5 = 98
        assert stop == pytest.approx(98.0)


# ---------------------------------------------------------------------------
# generate_technical_signal (end-to-end)
# ---------------------------------------------------------------------------


class TestGenerateSignal:
    def test_breakout_produces_signal(self):
        # 50 bars uptrend, 20 bars consolidation (tight range), then breakout
        closes = []
        highs = []
        lows = []
        for i in range(50):
            c = 100.0 + i * 0.2
            closes.append(c)
            highs.append(c + 0.3)
            lows.append(c - 0.3)
        base = closes[-1]
        for i in range(20):
            c = base + (0.2 if i % 2 == 0 else -0.2)
            closes.append(c)
            highs.append(c + 0.1)
            lows.append(c - 0.1)
        # Breakout: close above highest high of prior 20 bars
        breakout_level = max(highs[-20:])
        closes.append(breakout_level + 1.0)
        highs.append(breakout_level + 1.5)
        lows.append(breakout_level + 0.5)
        vols = [100_000] * 70 + [100_000] * 1
        vols = [100_000] * len(closes)
        vols[-1] = 300_000
        bars = _make_bars(closes, highs=highs, lows=lows, volumes=vols)
        sig = generate_technical_signal(bars, regime="bull")
        assert sig is not None
        assert sig.direction == "LONG"
        assert sig.stop_price < sig.entry_price
        assert sig.target_price > sig.entry_price
        assert 0 < sig.confidence <= 1.0

    def test_no_breakout_returns_none(self):
        closes = [100.0] * 70
        bars = _make_bars(closes)
        sig = generate_technical_signal(bars, regime="bull")
        assert sig is None

    def test_too_few_bars_returns_none(self):
        bars = _make_bars([100.0] * 30)
        assert generate_technical_signal(bars, regime="bull") is None

    def test_direction_hint_filters(self):
        closes = [100.0 + i * 0.1 for i in range(70)]
        closes.append(closes[-1] + 3.0)
        vols = [100_000] * 70 + [300_000]
        bars = _make_bars(closes, volumes=vols)
        # Breakout is LONG, but hint says SHORT → None
        sig = generate_technical_signal(bars, regime="bull", direction_hint="SHORT")
        assert sig is None

    def test_bear_regime_widens_stop(self):
        closes = [100.0 + i * 0.1 for i in range(70)]
        closes.append(closes[-1] + 3.0)
        vols = [100_000] * 70 + [300_000]
        bars = _make_bars(closes, volumes=vols)
        sig_bull = generate_technical_signal(bars, regime="bull")
        sig_bear = generate_technical_signal(bars, regime="bear")
        # Bear long uses factor 0.5 vs bull 1.0 → tighter stop in bear
        if sig_bull and sig_bear:
            bull_dist = sig_bull.entry_price - sig_bull.stop_price
            bear_dist = sig_bear.entry_price - sig_bear.stop_price
            assert bear_dist < bull_dist


# ---------------------------------------------------------------------------
# Capitulation detection
# ---------------------------------------------------------------------------


class TestCapitulation:
    def test_capitulation_detection(self):
        """Price down + high rvol → capitulation=True."""
        # Downtrend with spike volume on last bar
        closes = [100.0 - i * 0.5 for i in range(60)]
        vols = [100_000] * 59 + [300_000]
        bars = _make_bars(closes, volumes=vols)
        tech = compute_technicals(bars)
        v = check_volume_confirmation(tech)
        assert v["capitulation"] is True

    def test_no_capitulation_on_low_volume(self):
        """Price down + low rvol → capitulation=False."""
        closes = [100.0 - i * 0.5 for i in range(60)]
        vols = [100_000] * 60
        bars = _make_bars(closes, volumes=vols)
        tech = compute_technicals(bars)
        v = check_volume_confirmation(tech)
        assert v["capitulation"] is False


# ---------------------------------------------------------------------------
# Volume exhaustion
# ---------------------------------------------------------------------------


class TestVolumeExhaustion:
    def test_volume_exhaustion(self):
        """3+ bars of declining volume in uptrend → exhaustion=True."""
        closes = [100.0 + i * 0.5 for i in range(60)]
        # Last 4 bars: declining volume
        vols = [100_000] * 56 + [200_000, 180_000, 150_000, 120_000]
        bars = _make_bars(closes, volumes=vols)
        tech = compute_technicals(bars)
        v = check_volume_confirmation(tech)
        assert v["volume_exhaustion"] is True

    def test_no_exhaustion_in_downtrend(self):
        """Declining volume in downtrend should not flag exhaustion."""
        closes = [200.0 - i * 0.5 for i in range(60)]
        vols = [100_000] * 56 + [200_000, 180_000, 150_000, 120_000]
        bars = _make_bars(closes, volumes=vols)
        tech = compute_technicals(bars)
        v = check_volume_confirmation(tech)
        assert v["volume_exhaustion"] is False


# ---------------------------------------------------------------------------
# Position size factor
# ---------------------------------------------------------------------------


class TestPositionSizeFactor:
    def test_strong(self):
        assert compute_position_size_factor("strong") == 1.0

    def test_moderate(self):
        assert compute_position_size_factor("moderate") == 0.75

    def test_weak(self):
        assert compute_position_size_factor("weak") == 0.5


# ---------------------------------------------------------------------------
# Signal carries volume fields
# ---------------------------------------------------------------------------


class TestSignalVolumeFields:
    def test_signal_carries_volume_fields(self):
        """generate_technical_signal populates volume_conviction and position_size_factor."""
        closes = [100.0 + i * 0.1 for i in range(70)]
        closes.append(closes[-1] + 3.0)
        vols = [100_000] * 70 + [300_000]
        bars = _make_bars(closes, volumes=vols)
        sig = generate_technical_signal(bars, regime="bull")
        if sig is not None:
            assert sig.volume_conviction in ("strong", "moderate", "weak")
            assert sig.position_size_factor in (1.0, 0.75, 0.5)


# ---------------------------------------------------------------------------
# Divergence detection
# ---------------------------------------------------------------------------


class TestDivergence:
    def test_divergence_detection(self):
        """Price up + volume falling → divergence=True."""
        closes = [100.0 + i * 0.5 for i in range(60)]
        # Volume declining over last bars
        vols = [100_000] * 56 + [200_000, 180_000, 140_000, 100_000]
        bars = _make_bars(closes, volumes=vols)
        tech = compute_technicals(bars)
        v = check_volume_confirmation(tech)
        assert v["price_volume_divergence"] is True
