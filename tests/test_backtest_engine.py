"""Tests for backtest/engine.py — integration-level tests with synthetic data."""

import pytest

from backtest.data_feed import HistoricalDataFeed
from backtest.engine import BacktestConfig, BacktestEngine
from config.settings import RiskConfig


def _synthetic_feed():
    """Build a feed with SPY daily + a stock with daily and 5m bars."""
    bars = []
    base_ts = 1704067200  # 2024-01-01 00:00 UTC

    # SPY daily bars (65 days for regime detection)
    for d in range(70):
        ts = base_ts + d * 86400
        bars.append({
            "symbol": "SPY", "timeframe": "1d", "timestamp": ts,
            "open": 470 + d * 0.1, "high": 472 + d * 0.1,
            "low": 468 + d * 0.1, "close": 471 + d * 0.1,
            "volume": 50_000_000,
        })

    # VIX daily bars
    for d in range(70):
        ts = base_ts + d * 86400
        bars.append({
            "symbol": "VIX", "timeframe": "1d", "timestamp": ts,
            "open": 14, "high": 15, "low": 13, "close": 14,
            "volume": 1_000_000,
        })

    # TEST stock daily + 5m for last 5 days
    for d in range(70):
        ts = base_ts + d * 86400
        bars.append({
            "symbol": "TEST", "timeframe": "1d", "timestamp": ts,
            "open": 50 + d * 0.05, "high": 52 + d * 0.05,
            "low": 48 + d * 0.05, "close": 51 + d * 0.05,
            "volume": 5_000_000,
        })

    # 5m bars for TEST on a few days
    for d in range(65, 70):
        day_ts = base_ts + d * 86400
        for i in range(78):  # ~6.5 hours of 5m bars
            ts = day_ts + 34200 + i * 300  # start at 9:30 ET offset
            bars.append({
                "symbol": "TEST", "timeframe": "5m", "timestamp": ts,
                "open": 50 + d * 0.05, "high": 52 + d * 0.05,
                "low": 48 + d * 0.05, "close": 51 + d * 0.05,
                "volume": 50_000,
            })

    feed = HistoricalDataFeed()
    feed.load_bars_direct(bars)
    return feed


@pytest.mark.asyncio
async def test_engine_runs_without_error():
    """Smoke test: engine runs on synthetic data and returns results."""
    feed = _synthetic_feed()
    config = BacktestConfig(
        initial_capital=10_000,
        risk_config=RiskConfig(strategy_capital=10_000),
        universe_size=10,
        primary_timeframe="5m",
    )
    engine = BacktestEngine(config, feed)
    results = await engine.run()
    assert results is not None
    summary = results.summary()
    assert "BACKTEST RESULTS" in summary


@pytest.mark.asyncio
async def test_engine_no_data():
    """Engine handles empty feed gracefully."""
    feed = HistoricalDataFeed()
    config = BacktestConfig(initial_capital=10_000)
    engine = BacktestEngine(config, feed)
    results = await engine.run()
    assert results is not None


@pytest.mark.asyncio
async def test_engine_equity_preserved_no_trades():
    """With no signals generated, equity should equal initial capital."""
    # Use data that won't generate signals (flat, low volume)
    bars = []
    base_ts = 1704067200
    for d in range(70):
        ts = base_ts + d * 86400
        for sym in ("SPY", "VIX"):
            bars.append({
                "symbol": sym, "timeframe": "1d", "timestamp": ts,
                "open": 100, "high": 100, "low": 100, "close": 100,
                "volume": 1000,
            })
    feed = HistoricalDataFeed()
    feed.load_bars_direct(bars)

    config = BacktestConfig(initial_capital=10_000)
    engine = BacktestEngine(config, feed)
    results = await engine.run()

    # No intraday bars → no trades → equity = initial
    if results._snapshots:
        assert results._snapshots[-1].equity == 10_000
