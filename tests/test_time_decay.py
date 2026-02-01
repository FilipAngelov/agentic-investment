"""Tests for TimeDecayManager."""

import pytest

from data.models import Position, TimeDecaySignal
from execution.time_decay import TimeDecayManager


def _pos(
    symbol: str = "AAPL",
    direction: str = "LONG",
    entry_price: float = 100.0,
    stop_price: float = 97.0,
    entry_time: int = 0,
) -> Position:
    return Position(
        symbol=symbol,
        direction=direction,
        shares=10,
        entry_price=entry_price,
        entry_time=entry_time,
        current_price=entry_price,
        stop_price=stop_price,
        unrealized_pnl=0.0,
    )


HOUR = 3600
DAY = 86400
ATR = 2.0


@pytest.fixture
def mgr() -> TimeDecayManager:
    return TimeDecayManager()


class TestCheck:
    def test_fresh_position_returns_none(self, mgr: TimeDecayManager):
        pos = _pos(entry_time=0)
        assert mgr.check(pos, 100.0, ATR, HOUR) is None

    def test_4h_low_profit_long_tighten(self, mgr: TimeDecayManager):
        pos = _pos(entry_time=0, entry_price=100.0, stop_price=97.0)
        # price=100.5 => profit_atr=0.25 < 0.5
        sig = mgr.check(pos, 100.5, ATR, 4 * HOUR)
        assert sig is not None
        assert sig.action == "tighten_stop"
        assert sig.reason == "thesis_not_confirmed"
        assert sig.new_stop == 100.0  # breakeven

    def test_4h_sufficient_profit_returns_none(self, mgr: TimeDecayManager):
        pos = _pos(entry_time=0, entry_price=100.0, stop_price=97.0)
        # price=101.0 => profit_atr=0.5, not < 0.5
        assert mgr.check(pos, 101.0, ATR, 4 * HOUR) is None

    def test_3d_low_profit_exit(self, mgr: TimeDecayManager):
        pos = _pos(entry_time=0, entry_price=100.0, stop_price=97.0)
        # price=101.5 => profit_atr=0.75 < 1.0
        sig = mgr.check(pos, 101.5, ATR, 3 * DAY)
        assert sig is not None
        assert sig.action == "exit"
        assert sig.reason == "stalled_momentum"

    def test_3d_sufficient_profit_returns_none(self, mgr: TimeDecayManager):
        pos = _pos(entry_time=0, entry_price=100.0, stop_price=97.0)
        # price=102.0 => profit_atr=1.0, not < 1.0
        assert mgr.check(pos, 102.0, ATR, 3 * DAY) is None

    def test_5d_profitable_force_exit(self, mgr: TimeDecayManager):
        pos = _pos(entry_time=0, entry_price=100.0, stop_price=97.0)
        sig = mgr.check(pos, 110.0, ATR, 5 * DAY)
        assert sig is not None
        assert sig.action == "exit"
        assert sig.reason == "max_hold_exceeded"

    def test_5d_losing_force_exit(self, mgr: TimeDecayManager):
        pos = _pos(entry_time=0, entry_price=100.0, stop_price=97.0)
        sig = mgr.check(pos, 95.0, ATR, 5 * DAY)
        assert sig is not None
        assert sig.action == "exit"
        assert sig.reason == "max_hold_exceeded"

    def test_short_mirror(self, mgr: TimeDecayManager):
        pos = _pos(direction="SHORT", entry_price=100.0, stop_price=103.0, entry_time=0)
        # price=99.8 => profit_atr=0.1 < 0.5, tighten stop to breakeven
        sig = mgr.check(pos, 99.8, ATR, 4 * HOUR)
        assert sig is not None
        assert sig.action == "tighten_stop"
        assert sig.new_stop == 100.0  # min(100, 103) = 100

    def test_stop_already_at_breakeven_returns_none(self, mgr: TimeDecayManager):
        # Stop already at entry (breakeven)
        pos = _pos(entry_time=0, entry_price=100.0, stop_price=100.0)
        # profit_atr=0.25 < 0.5, but stop already at breakeven
        assert mgr.check(pos, 100.5, ATR, 4 * HOUR) is None

    def test_zero_atr_returns_none(self, mgr: TimeDecayManager):
        pos = _pos(entry_time=0)
        assert mgr.check(pos, 100.0, 0.0, 5 * DAY) is None

    def test_negative_atr_returns_none(self, mgr: TimeDecayManager):
        pos = _pos(entry_time=0)
        assert mgr.check(pos, 100.0, -1.0, 5 * DAY) is None

    def test_priority_5d_overrides_3d(self, mgr: TimeDecayManager):
        """5d rule fires even though 3d rule also matches."""
        pos = _pos(entry_time=0, entry_price=100.0, stop_price=97.0)
        # profit_atr=0.25 < 1.0 (matches 3d rule too) but 5d should win
        sig = mgr.check(pos, 100.5, ATR, 5 * DAY)
        assert sig is not None
        assert sig.reason == "max_hold_exceeded"


class TestCheckAll:
    def test_batch_returns_qualifying_only(self, mgr: TimeDecayManager):
        positions = {
            "AAPL": _pos("AAPL", entry_time=0, entry_price=100.0, stop_price=97.0),
            "MSFT": _pos("MSFT", entry_time=0, entry_price=50.0, stop_price=48.0),
        }
        prices = {"AAPL": 100.5, "MSFT": 55.0}  # MSFT profitable
        atrs = {"AAPL": ATR, "MSFT": ATR}
        now = 5 * DAY
        signals = mgr.check_all(positions, prices, atrs, now)
        syms = {s.symbol for s in signals}
        # Both at 5d → both should exit (max hold)
        assert "AAPL" in syms
        assert "MSFT" in syms

    def test_batch_skips_missing_data(self, mgr: TimeDecayManager):
        positions = {
            "AAPL": _pos("AAPL", entry_time=0),
        }
        signals = mgr.check_all(positions, {}, {}, 5 * DAY)
        assert signals == []
