"""Tests for backtest/data_feed.py."""

from backtest.data_feed import HistoricalDataFeed


def _make_bars():
    """Create sample bars for AAPL across 1d and 5m."""
    bars = []
    # 3 daily bars
    for i, ts in enumerate([1000, 86400 + 1000, 2 * 86400 + 1000]):
        bars.append({
            "symbol": "AAPL", "timeframe": "1d", "timestamp": ts,
            "open": 100 + i, "high": 105 + i, "low": 95 + i,
            "close": 102 + i, "volume": 10000,
        })
    # 5m bars within day 1 (ts=1000)
    for j in range(5):
        bars.append({
            "symbol": "AAPL", "timeframe": "5m", "timestamp": 1000 + j * 300,
            "open": 100, "high": 101, "low": 99, "close": 100.5,
            "volume": 1000,
        })
    # 5m bars within day 2 (ts=86400+1000)
    for j in range(3):
        bars.append({
            "symbol": "AAPL", "timeframe": "5m", "timestamp": 86400 + 1000 + j * 300,
            "open": 101, "high": 102, "low": 100, "close": 101.5,
            "volume": 1000,
        })
    return bars


class TestHistoricalDataFeed:
    def test_load_bars_direct(self):
        feed = HistoricalDataFeed()
        feed.load_bars_direct(_make_bars())
        assert "AAPL" in feed.all_symbols()

    def test_daily_timestamps(self):
        feed = HistoricalDataFeed()
        feed.load_bars_direct(_make_bars())
        daily = feed.daily_timestamps()
        assert len(daily) == 3
        assert daily == sorted(daily)

    def test_intraday_timestamps(self):
        feed = HistoricalDataFeed()
        feed.load_bars_direct(_make_bars())
        intraday = feed.intraday_timestamps(1000, "5m")
        assert len(intraday) == 5

    def test_intraday_timestamps_day2(self):
        feed = HistoricalDataFeed()
        feed.load_bars_direct(_make_bars())
        intraday = feed.intraday_timestamps(86400 + 1000, "5m")
        assert len(intraday) == 3

    def test_get_bars_up_to_no_lookahead(self):
        feed = HistoricalDataFeed()
        feed.load_bars_direct(_make_bars())
        # At ts=1600 (after 3 5m bars), should only see bars up to 1600
        bars = feed.get_bars_up_to("AAPL", "5m", 1600, lookback=100)
        for b in bars:
            assert b["timestamp"] <= 1600

    def test_get_bars_up_to_lookback_limit(self):
        feed = HistoricalDataFeed()
        feed.load_bars_direct(_make_bars())
        bars = feed.get_bars_up_to("AAPL", "5m", 99999, lookback=2)
        assert len(bars) == 2

    def test_get_bar_at_exact(self):
        feed = HistoricalDataFeed()
        feed.load_bars_direct(_make_bars())
        bar = feed.get_bar_at("AAPL", "5m", 1000)
        assert bar is not None
        assert bar["timestamp"] == 1000

    def test_get_bar_at_missing(self):
        feed = HistoricalDataFeed()
        feed.load_bars_direct(_make_bars())
        bar = feed.get_bar_at("AAPL", "5m", 9999)
        assert bar is None

    def test_symbols_at(self):
        feed = HistoricalDataFeed()
        feed.load_bars_direct(_make_bars())
        syms = feed.symbols_at("5m", 1000)
        assert "AAPL" in syms

    def test_empty_feed(self):
        feed = HistoricalDataFeed()
        assert feed.daily_timestamps() == []
        assert feed.all_symbols() == set()
        assert feed.get_bars_up_to("X", "1d", 0) == []
