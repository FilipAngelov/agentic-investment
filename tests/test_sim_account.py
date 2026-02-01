"""Tests for backtest/sim_account.py."""

from backtest.sim_account import SimulatedAccount
from data.models import Position


def _pos(symbol="AAPL", price=150.0, shares=10, entry=145.0):
    return Position(
        symbol=symbol, direction="LONG", shares=shares,
        entry_price=entry, entry_time=1000, current_price=price,
        stop_price=entry - 5, unrealized_pnl=(price - entry) * shares,
    )


class TestSimulatedAccount:
    def test_initial_state(self):
        sim = SimulatedAccount(10_000)
        assert sim.cash == 10_000
        assert sim.equity({}) == 10_000
        assert sim.daily_pnl == 0.0

    def test_buy_reduces_cash(self):
        sim = SimulatedAccount(10_000)
        sim.on_fill_buy(10, 100.0, 0.50)
        assert sim.cash == 10_000 - 1000 - 0.50

    def test_sell_increases_cash(self):
        sim = SimulatedAccount(10_000)
        sim.on_fill_buy(10, 100.0, 0.50)
        sim.on_fill_sell(10, 110.0, 0.50, pnl=100.0)
        # cash = 10000 - 1000.50 + 1100 - 0.50 = 10099
        assert abs(sim.cash - 10099.0) < 0.01

    def test_equity_includes_positions(self):
        sim = SimulatedAccount(10_000)
        sim.on_fill_buy(10, 100.0, 0.0)
        pos = _pos(price=110.0, shares=10, entry=100.0)
        # cash = 9000, position value = 10*110 = 1100
        eq = sim.equity({"AAPL": pos})
        assert abs(eq - 10100.0) < 0.01

    def test_account_state_cash_only(self):
        sim = SimulatedAccount(10_000)
        state = sim.account_state({})
        assert state.buying_power == 10_000
        assert state.total_cash_value == 10_000
        assert state.available_funds == 10_000
        assert state.day_trades_remaining == -1

    def test_new_day_resets_pnl(self):
        sim = SimulatedAccount(10_000)
        sim.on_fill_buy(10, 100.0, 0.50)
        sim.on_fill_sell(10, 110.0, 0.50, pnl=100.0)
        assert sim.daily_pnl != 0.0
        sim.new_day({})
        assert sim.daily_pnl == 0.0

    def test_commissions_tracked(self):
        sim = SimulatedAccount(10_000)
        sim.on_fill_buy(100, 50.0, 0.50)
        sim.on_fill_sell(100, 55.0, 0.50, pnl=500.0)
        assert sim.total_commissions == 1.0
