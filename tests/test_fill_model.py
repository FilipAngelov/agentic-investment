"""Tests for backtest/fill_model.py."""

from backtest.fill_model import FillModel


def _bar(open_=100, high=105, low=95, close=102, volume=10000):
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


class TestEntryFill:
    def test_long_entry_fills_when_bar_covers_limit(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_entry_fill(100.0, "LONG", 50, _bar(low=98))
        assert result.filled
        assert result.fill_price == 100.0
        assert result.fill_shares == 50

    def test_long_entry_no_fill_when_bar_above_limit(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_entry_fill(94.0, "LONG", 50, _bar(low=95))
        assert not result.filled

    def test_short_entry_fills_when_bar_covers_limit(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_entry_fill(104.0, "SHORT", 50, _bar(high=105))
        assert result.filled
        assert result.fill_price == 104.0

    def test_short_entry_no_fill_when_bar_below_limit(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_entry_fill(106.0, "SHORT", 50, _bar(high=105))
        assert not result.filled

    def test_slippage_worsens_entry(self):
        fm = FillModel(slippage_bps=100, seed=1)  # 1% slippage
        result = fm.try_entry_fill(100.0, "LONG", 50, _bar(low=95))
        assert result.filled
        assert result.fill_price > 100.0  # adverse slippage

    def test_commission_min(self):
        fm = FillModel(slippage_bps=0, commission_per_share=0.005, seed=1)
        result = fm.try_entry_fill(100.0, "LONG", 10, _bar(low=95))
        assert result.commission == 0.35  # min commission

    def test_commission_normal(self):
        fm = FillModel(slippage_bps=0, commission_per_share=0.005, seed=1)
        result = fm.try_entry_fill(100.0, "LONG", 200, _bar(low=95))
        assert result.commission == 1.0  # 200 * 0.005


class TestStopFill:
    def test_long_stop_fills_on_breach(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_stop_fill(96.0, "LONG", 50, _bar(open_=100, low=95))
        assert result.filled
        assert result.fill_price == 96.0

    def test_long_stop_gap_through(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_stop_fill(100.0, "LONG", 50, _bar(open_=95, low=93))
        assert result.filled
        assert result.fill_price == 95.0  # fills at open

    def test_long_stop_no_fill(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_stop_fill(94.0, "LONG", 50, _bar(low=95))
        assert not result.filled

    def test_short_stop_fills_on_breach(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_stop_fill(104.0, "SHORT", 50, _bar(open_=100, high=105))
        assert result.filled

    def test_short_stop_gap_through(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_stop_fill(100.0, "SHORT", 50, _bar(open_=106, high=108))
        assert result.filled
        assert result.fill_price == 106.0

    def test_stops_always_fill_fully(self):
        fm = FillModel(slippage_bps=0, partial_fill_prob=1.0, seed=1)
        result = fm.try_stop_fill(96.0, "LONG", 50, _bar(low=95))
        assert result.fill_shares == 50  # no partial on stops


class TestTargetFill:
    def test_long_target_fills(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_target_fill(104.0, "LONG", 50, _bar(high=105))
        assert result.filled
        assert result.fill_price == 104.0

    def test_long_target_no_fill(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_target_fill(106.0, "LONG", 50, _bar(high=105))
        assert not result.filled

    def test_short_target_fills(self):
        fm = FillModel(slippage_bps=0, seed=1)
        result = fm.try_target_fill(96.0, "SHORT", 50, _bar(low=95))
        assert result.filled

    def test_target_conservative_slippage(self):
        fm = FillModel(slippage_bps=100, seed=1)  # 1%
        result = fm.try_target_fill(104.0, "LONG", 50, _bar(high=110))
        assert result.filled
        # Conservative: fill at target - slippage (slightly worse than target)
        assert result.fill_price < 104.0


class TestPartialFills:
    def test_partial_fill_reduces_shares(self):
        fm = FillModel(slippage_bps=0, partial_fill_prob=1.0, seed=42)
        result = fm.try_entry_fill(100.0, "LONG", 100, _bar(low=95))
        assert result.filled
        assert 50 <= result.fill_shares <= 90

    def test_no_partial_when_prob_zero(self):
        fm = FillModel(slippage_bps=0, partial_fill_prob=0.0, seed=1)
        result = fm.try_entry_fill(100.0, "LONG", 100, _bar(low=95))
        assert result.fill_shares == 100
