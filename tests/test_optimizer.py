"""Tests for backtest/optimizer.py — walk-forward optimizer."""

import pytest

from backtest.data_feed import HistoricalDataFeed
from backtest.optimizer import WFOConfig, WalkForwardOptimizer, _add_months, _find_nearest
from backtest.param_space import ParamRange, ParamSpace
from datetime import datetime, timezone


def _build_feed(days: int = 500, start_year: int = 2024) -> HistoricalDataFeed:
    """Build a minimal synthetic feed with daily + 5m bars."""
    bars = []
    base_ts = int(datetime(start_year, 1, 1, tzinfo=timezone.utc).timestamp())

    for d in range(days):
        ts = base_ts + d * 86400
        for sym in ("SPY", "VIX"):
            bars.append({
                "symbol": sym, "timeframe": "1d", "timestamp": ts,
                "open": 100, "high": 101, "low": 99, "close": 100,
                "volume": 1_000_000,
            })
        # A tradeable stock
        bars.append({
            "symbol": "AAPL", "timeframe": "1d", "timestamp": ts,
            "open": 150 + d * 0.01, "high": 152 + d * 0.01,
            "low": 148 + d * 0.01, "close": 151 + d * 0.01,
            "volume": 5_000_000,
        })

    feed = HistoricalDataFeed()
    feed.load_bars_direct(bars)
    return feed


class TestAddMonths:
    def test_basic(self):
        dt = datetime(2024, 1, 15, tzinfo=timezone.utc)
        result = _add_months(dt, 3)
        assert result.month == 4
        assert result.year == 2024

    def test_year_wrap(self):
        dt = datetime(2024, 11, 1, tzinfo=timezone.utc)
        result = _add_months(dt, 3)
        assert result.month == 2
        assert result.year == 2025

    def test_day_clamp(self):
        dt = datetime(2024, 1, 31, tzinfo=timezone.utc)
        result = _add_months(dt, 1)
        assert result.month == 2
        assert result.day == 29  # 2024 is leap year


class TestFindNearest:
    def test_exact(self):
        assert _find_nearest([10, 20, 30], 20) == 20

    def test_between(self):
        assert _find_nearest([10, 20, 30], 14) == 10
        assert _find_nearest([10, 20, 30], 16) == 20

    def test_before_all(self):
        assert _find_nearest([10, 20, 30], 5) == 10

    def test_after_all(self):
        assert _find_nearest([10, 20, 30], 35) == 30


class TestFoldGeneration:
    def test_generates_folds(self):
        feed = _build_feed(days=600)
        wfo = WFOConfig(train_months=12, test_months=3, n_samples=2)
        ps = ParamSpace([ParamRange("stop_k1", 1.5, 3.0, 0.5)])
        opt = WalkForwardOptimizer(wfo, ps, feed)
        folds = opt._generate_folds(feed.daily_timestamps())
        assert len(folds) >= 1
        # Each fold: train_start < train_end <= test_start < test_end
        for tr_s, tr_e, te_s, te_e in folds:
            assert tr_s < tr_e
            assert te_s < te_e
            assert tr_e <= te_s

    def test_no_folds_short_data(self):
        feed = _build_feed(days=30)
        wfo = WFOConfig(train_months=12, test_months=3)
        ps = ParamSpace([ParamRange("stop_k1", 1.5, 3.0, 0.5)])
        opt = WalkForwardOptimizer(wfo, ps, feed)
        folds = opt._generate_folds(feed.daily_timestamps())
        assert len(folds) == 0

    def test_test_periods_dont_overlap(self):
        feed = _build_feed(days=700)
        wfo = WFOConfig(train_months=12, test_months=3)
        ps = ParamSpace([ParamRange("stop_k1", 1.5, 3.0, 0.5)])
        opt = WalkForwardOptimizer(wfo, ps, feed)
        folds = opt._generate_folds(feed.daily_timestamps())
        for i in range(len(folds) - 1):
            _, _, _, te_e = folds[i]
            _, _, te_s_next, _ = folds[i + 1]
            assert te_e <= te_s_next


@pytest.mark.asyncio
async def test_optimizer_smoke():
    """Smoke test: run WFO with minimal params on synthetic data, 2 folds."""
    feed = _build_feed(days=600)
    wfo = WFOConfig(train_months=12, test_months=3, n_samples=2, seed=42)
    ps = ParamSpace([ParamRange("stop_k1", 1.5, 3.0, 1.5)])  # only 2 values
    opt = WalkForwardOptimizer(wfo, ps, feed)
    results = await opt.run()
    assert len(results.folds) >= 1
    agg = results.aggregate()
    assert "n_folds" in agg
    assert "is_overfit" in agg


@pytest.mark.asyncio
async def test_optimizer_empty_feed():
    """Empty feed produces no folds."""
    feed = HistoricalDataFeed()
    wfo = WFOConfig(n_samples=2)
    ps = ParamSpace.default()
    opt = WalkForwardOptimizer(wfo, ps, feed)
    results = await opt.run()
    assert len(results.folds) == 0
