"""Tests for execution/trailing_stop.py — adaptive trailing stop."""

from __future__ import annotations

import pytest

from data.models import Position, StopUpdate
from execution.trailing_stop import TrailingStopManager


def _pos(
    symbol: str = "AAPL",
    direction: str = "LONG",
    entry: float = 100.0,
    stop: float = 96.0,
    current: float = 100.0,
) -> Position:
    return Position(
        symbol=symbol,
        direction=direction,
        shares=10,
        entry_price=entry,
        entry_time=0,
        current_price=current,
        stop_price=stop,
        unrealized_pnl=0.0,
    )


ATR = 2.0  # constant ATR for most tests


class TestSelectK:
    def test_no_profit(self):
        mgr = TrailingStopManager()
        assert mgr._select_k(0.5) == 2.0  # k1

    def test_1_atr(self):
        mgr = TrailingStopManager()
        assert mgr._select_k(1.5) == 1.5  # k2

    def test_2_atr(self):
        mgr = TrailingStopManager()
        assert mgr._select_k(2.5) == 1.0  # k3

    def test_3_atr(self):
        mgr = TrailingStopManager()
        assert mgr._select_k(3.5) == 0.75  # k4


class TestLongStops:
    def test_no_profit_initial_stop(self):
        mgr = TrailingStopManager()
        pos = _pos(entry=100, stop=96, current=100)
        # price=100, atr=2, profit_atr=0 → k1=2.0, candidate=100-4=96
        upd = mgr.update_stop(pos, 100.0, ATR, "bull")
        assert upd.new_stop == 96.0
        assert not upd.moved

    def test_1_5_atr_profit_tightens(self):
        mgr = TrailingStopManager()
        pos = _pos(entry=100, stop=96, current=103)
        # profit_atr=3/2=1.5 → k2=1.5, candidate=103-3=100
        upd = mgr.update_stop(pos, 103.0, ATR, "bull")
        assert upd.new_stop == 100.0
        assert upd.moved
        assert upd.k_used == 1.5

    def test_2_5_atr_profit(self):
        mgr = TrailingStopManager()
        pos = _pos(entry=100, stop=100, current=105)
        # profit_atr=5/2=2.5 → k3=1.0, candidate=105-2=103
        upd = mgr.update_stop(pos, 105.0, ATR, "bull")
        assert upd.new_stop == 103.0
        assert upd.moved

    def test_3_5_atr_profit(self):
        mgr = TrailingStopManager()
        pos = _pos(entry=100, stop=103, current=107)
        # profit_atr=7/2=3.5 → k4=0.75, candidate=107-1.5=105.5
        upd = mgr.update_stop(pos, 107.0, ATR, "bull")
        assert upd.new_stop == 105.5
        assert upd.moved
        assert upd.k_used == 0.75

    def test_ratchet_never_moves_down(self):
        mgr = TrailingStopManager()
        # Stop already at 103, price pulls back to 102
        pos = _pos(entry=100, stop=103, current=102)
        # profit_atr=2/2=1 → k2=1.5, candidate=102-3=99 < 103
        upd = mgr.update_stop(pos, 102.0, ATR, "bull")
        assert upd.new_stop == 103.0
        assert not upd.moved


class TestShortStops:
    def test_short_mirror_logic(self):
        mgr = TrailingStopManager()
        pos = _pos(direction="SHORT", entry=100, stop=104, current=97)
        # profit_atr=(100-97)/2=1.5 → k2=1.5, candidate=97+3=100
        upd = mgr.update_stop(pos, 97.0, ATR, "bull")
        assert upd.new_stop == 100.0
        assert upd.moved

    def test_short_ratchet_never_moves_up(self):
        mgr = TrailingStopManager()
        pos = _pos(direction="SHORT", entry=100, stop=100, current=99)
        # profit_atr=0.5 → k1=2.0, candidate=99+4=103 > 100 → stays 100
        upd = mgr.update_stop(pos, 99.0, ATR, "bull")
        assert upd.new_stop == 100.0
        assert not upd.moved


class TestRegimeAdjustment:
    def test_strong_bull_wider_stops(self):
        mgr = TrailingStopManager()
        pos = _pos(entry=100, stop=90, current=103)
        # profit_atr=1.5 → k2=1.5, rf=1.5, adjusted=2.25, candidate=103-4.5=98.5
        upd = mgr.update_stop(pos, 103.0, ATR, "strong_bull")
        assert upd.new_stop == 98.5
        assert upd.regime_factor == 1.5

    def test_choppy_tighter_stops(self):
        mgr = TrailingStopManager()
        pos = _pos(entry=100, stop=90, current=103)
        # rf=0.6, adjusted_k=1.5*0.6=0.9, candidate=103-1.8=101.2
        upd = mgr.update_stop(pos, 103.0, ATR, "choppy")
        assert upd.new_stop == 101.2
        assert upd.regime_factor == 0.6

    def test_bear_long_uses_0_5(self):
        mgr = TrailingStopManager()
        pos = _pos(direction="LONG", entry=100, stop=90, current=103)
        upd = mgr.update_stop(pos, 103.0, ATR, "bear")
        assert upd.regime_factor == 0.5

    def test_bear_short_uses_0_8(self):
        mgr = TrailingStopManager()
        pos = _pos(direction="SHORT", entry=100, stop=110, current=97)
        upd = mgr.update_stop(pos, 97.0, ATR, "bear")
        assert upd.regime_factor == 0.8


class TestUpdateAll:
    def test_batch_returns_only_moves(self):
        mgr = TrailingStopManager()
        positions = {
            "AAPL": _pos("AAPL", entry=100, stop=96, current=100),  # no move
            "MSFT": _pos("MSFT", entry=50, stop=46, current=55),  # moves
        }
        prices = {"AAPL": 100.0, "MSFT": 55.0}
        atrs = {"AAPL": 2.0, "MSFT": 2.0}
        results = mgr.update_all(positions, prices, atrs, "bull")
        symbols = [r.symbol for r in results]
        assert "MSFT" in symbols
        assert "AAPL" not in symbols


class TestEdgeCases:
    def test_zero_atr_returns_unchanged(self):
        mgr = TrailingStopManager()
        pos = _pos(entry=100, stop=96, current=105)
        upd = mgr.update_stop(pos, 105.0, 0.0, "bull")
        assert upd.new_stop == 96.0
        assert not upd.moved

    def test_negative_atr_returns_unchanged(self):
        mgr = TrailingStopManager()
        pos = _pos(entry=100, stop=96, current=105)
        upd = mgr.update_stop(pos, 105.0, -1.0, "bull")
        assert upd.new_stop == 96.0
        assert not upd.moved
