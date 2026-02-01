"""Unit tests for signals/timeframe.py — multi-timeframe alignment."""

import pytest

from signals.timeframe import check_timeframe_alignment, compute_timeframe_confidence_adjustment


def _make_bars(closes, symbol="TEST"):
    """Build synthetic bar dicts from close prices."""
    n = len(closes)
    bars = []
    for i in range(n):
        bars.append({
            "symbol": symbol,
            "timestamp": 1_000_000 + i * 60,
            "open": closes[i],
            "high": closes[i] + 1.0,
            "low": closes[i] - 1.0,
            "close": closes[i],
            "volume": 100_000,
        })
    return bars


def _trending_up(n=80, start=100.0, step=0.5):
    return _make_bars([start + i * step for i in range(n)])


def _trending_down(n=80, start=200.0, step=0.5):
    return _make_bars([start - i * step for i in range(n)])


def _flat(n=80, price=100.0):
    return _make_bars([price] * n)


class TestCheckTimeframeAlignment:
    def test_all_timeframes_aligned_long(self):
        result = check_timeframe_alignment({
            "15m": _trending_up(),
            "1d": _trending_up(),
        })
        assert result["aligned"] is True
        assert result["alignment_score"] == 1.0
        assert result["dominant_direction"] == "LONG"

    def test_all_timeframes_aligned_short(self):
        result = check_timeframe_alignment({
            "15m": _trending_down(),
            "1d": _trending_down(),
        })
        assert result["aligned"] is True
        assert result["alignment_score"] == 1.0
        assert result["dominant_direction"] == "SHORT"

    def test_partial_alignment(self):
        result = check_timeframe_alignment({
            "15m": _trending_up(),
            "1d": _flat(),
        })
        assert result["aligned"] is False
        assert result["alignment_score"] == 0.5

    def test_no_alignment(self):
        result = check_timeframe_alignment({
            "15m": _trending_up(),
            "1d": _trending_down(),
        })
        assert result["aligned"] is False
        assert result["alignment_score"] == 0.5

    def test_insufficient_bars_skipped(self):
        short_bars = _make_bars([100.0] * 20)
        result = check_timeframe_alignment({
            "15m": _trending_up(),
            "1d": short_bars,
        })
        # Only 15m counted (1d has <51 bars)
        assert "1d" not in result["details"]
        assert "15m" in result["details"]

    def test_all_insufficient_bars(self):
        result = check_timeframe_alignment({
            "15m": _make_bars([100.0] * 10),
            "1d": _make_bars([100.0] * 10),
        })
        assert result["aligned"] is False
        assert result["alignment_score"] == 0.0
        assert result["dominant_direction"] is None


class TestConfidenceAdjustment:
    def test_confidence_boost_full_alignment(self):
        alignment = check_timeframe_alignment({
            "15m": _trending_up(),
            "1d": _trending_up(),
        })
        adj = compute_timeframe_confidence_adjustment(alignment, "LONG")
        assert adj == pytest.approx(0.15)

    def test_confidence_penalty_opposed(self):
        alignment = check_timeframe_alignment({
            "15m": _trending_down(),
            "1d": _trending_down(),
        })
        adj = compute_timeframe_confidence_adjustment(alignment, "LONG")
        assert adj == pytest.approx(-0.15)

    def test_partial_boost(self):
        alignment = check_timeframe_alignment({
            "15m": _trending_up(),
            "1d": _flat(),
        })
        adj = compute_timeframe_confidence_adjustment(alignment, "LONG")
        assert adj == pytest.approx(0.05)

    def test_no_details_returns_zero(self):
        alignment = check_timeframe_alignment({
            "15m": _make_bars([100.0] * 10),
        })
        adj = compute_timeframe_confidence_adjustment(alignment, "LONG")
        assert adj == 0.0


class TestSignalIntegration:
    def test_signal_carries_alignment_field(self):
        from signals.technical import generate_technical_signal

        # Build bars that produce a LONG breakout
        closes = [100.0 + i * 0.1 for i in range(70)]
        closes.append(closes[-1] + 3.0)
        vols = [100_000] * 70 + [300_000]
        n = len(closes)
        bars = []
        for i in range(n):
            bars.append({
                "symbol": "TEST",
                "timestamp": 1_000_000 + i * 60,
                "open": closes[i],
                "high": closes[i] + 1.0,
                "low": closes[i] - 1.0,
                "close": closes[i],
                "volume": vols[i],
            })

        higher = {"15m": _trending_up(), "1d": _trending_up()}
        sig = generate_technical_signal(bars, regime="bull", higher_tf_bars=higher)
        if sig is not None:
            assert sig.timeframe_alignment is not None
            assert sig.timeframe_alignment == 1.0
            assert "tf_align" in sig.reason

    def test_signal_without_higher_tf_has_no_alignment(self):
        from signals.technical import generate_technical_signal

        closes = [100.0 + i * 0.1 for i in range(70)]
        closes.append(closes[-1] + 3.0)
        vols = [100_000] * 70 + [300_000]
        n = len(closes)
        bars = []
        for i in range(n):
            bars.append({
                "symbol": "TEST",
                "timestamp": 1_000_000 + i * 60,
                "open": closes[i],
                "high": closes[i] + 1.0,
                "low": closes[i] - 1.0,
                "close": closes[i],
                "volume": vols[i],
            })

        sig = generate_technical_signal(bars, regime="bull")
        if sig is not None:
            assert sig.timeframe_alignment is None
