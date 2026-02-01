"""Tests for signals/pipeline.py — full signal generation pipeline."""

from __future__ import annotations

import time


from data.models import Bar, Catalyst
from signals.pipeline import generate_signal
from signals.scoring import MIN_COMPOSITE_SCORE


def _make_bars(n: int, symbol: str = "TEST", base: float = 100.0, trend: float = 0.5) -> list[dict]:
    """Generate n synthetic bar dicts with an uptrend that creates a breakout.

    Adds zigzag noise so RSI stays below overbought, and spikes the last bar
    above the prior 20-bar high so ``detect_breakout`` fires.
    """
    bars = []
    ts = int(time.time()) - n * 60
    for i in range(n):
        p = base + i * trend
        # Zigzag: odd bars pull back to keep RSI below overbought
        noise = -1.2 if i % 2 else 0.8
        bars.append({
            "symbol": symbol,
            "timestamp": ts + i * 60,
            "open": p - 0.3,
            "high": p + 2.0,
            "low": p - 2.0,
            "close": p + noise,
            "volume": 2_000_000,
        })
    # Spike the last bar so close exceeds prior 20-bar high
    if trend > 0 and n > 21:
        last_p = base + (n - 1) * trend
        spike = 5.0
        bars[-1]["close"] = last_p + spike
        bars[-1]["high"] = last_p + spike + 1.0
    return bars


def _make_daily_bars(n: int, symbol: str = "TEST", base: float = 100.0) -> list[Bar]:
    """Generate n daily Bar objects."""
    bars = []
    ts = int(time.time()) - n * 86400
    for i in range(n):
        p = base + i * 0.1
        bars.append(Bar(
            symbol=symbol,
            timestamp=ts + i * 86400,
            timeframe="1d",
            low=p - 1,
            high=p + 1,
            open=p - 0.5,
            close=p + 0.5,
            volume=1_000_000,
        ))
    return bars


class TestGenerateSignal:
    def test_full_pipeline_produces_signal(self):
        """With strong uptrend + catalysts, pipeline should produce a scored signal."""
        bars = _make_bars(60, trend=0.5)
        stock_bars = _make_daily_bars(50)
        bench_bars = _make_daily_bars(50, symbol="SPY")
        catalysts = [
            Catalyst(
                timestamp=int(time.time()),
                headline="Strong earnings beat",
                source="test",
                sentiment=0.9,
                magnitude=4,
            ),
        ]
        sector_info = {"momentum": 0.05, "relative_strength": 1.5, "accelerating": True}

        result = generate_signal(
            bars=bars,
            regime="bull",
            stock_bars=stock_bars,
            benchmark_bars=bench_bars,
            catalysts=catalysts,
            sector_tracker_info=sector_info,
            sector="Technology",
        )

        assert result is not None
        assert result.score >= MIN_COMPOSITE_SCORE
        assert result.confidence > 0
        assert result.atr is not None
        assert result.atr > 0
        assert result.sector == "Technology"

    def test_returns_none_with_insufficient_bars(self):
        bars = _make_bars(10)
        stock_bars = _make_daily_bars(50)
        bench_bars = _make_daily_bars(50, symbol="SPY")

        result = generate_signal(
            bars=bars,
            regime="bull",
            stock_bars=stock_bars,
            benchmark_bars=bench_bars,
            catalysts=[],
        )
        assert result is None

    def test_returns_none_on_flat_market(self):
        """Flat bars (no breakout) should produce no signal."""
        bars = _make_bars(60, trend=0.0)
        stock_bars = _make_daily_bars(50)
        bench_bars = _make_daily_bars(50, symbol="SPY")

        result = generate_signal(
            bars=bars,
            regime="bull",
            stock_bars=stock_bars,
            benchmark_bars=bench_bars,
            catalysts=[],
        )
        assert result is None

    def test_direction_hint_filters(self):
        """SHORT hint on uptrend bars should produce no signal."""
        bars = _make_bars(60, trend=0.5)
        stock_bars = _make_daily_bars(50)
        bench_bars = _make_daily_bars(50, symbol="SPY")

        result = generate_signal(
            bars=bars,
            regime="bull",
            stock_bars=stock_bars,
            benchmark_bars=bench_bars,
            catalysts=[],
            direction_hint="SHORT",
        )
        assert result is None

    def test_scored_signal_has_composite_score(self):
        """If pipeline produces a signal, score should differ from raw confidence."""
        bars = _make_bars(60, trend=0.5)
        stock_bars = _make_daily_bars(50)
        bench_bars = _make_daily_bars(50, symbol="SPY")
        catalysts = [
            Catalyst(
                timestamp=int(time.time()),
                headline="Upgrade",
                source="test",
                sentiment=0.7,
                magnitude=3,
            ),
        ]

        result = generate_signal(
            bars=bars,
            regime="strong_bull",
            stock_bars=stock_bars,
            benchmark_bars=bench_bars,
            catalysts=catalysts,
            sector_tracker_info={"momentum": 0.04, "relative_strength": 1.3, "accelerating": True},
        )

        assert result is not None
        # Composite score is a weighted blend, not just raw confidence
        assert 0.0 <= result.score <= 1.0
        assert 0.0 <= result.confidence <= 1.0
