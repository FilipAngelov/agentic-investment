"""Tests for execution.sizing — PositionSizer."""

from __future__ import annotations

import time

import pytest

from config.settings import RiskConfig
from data.models import AccountState, Position, Signal
from execution.sizing import PositionSizer


def _make_signal(
    entry: float = 100.0,
    stop: float = 98.0,
    direction: str = "LONG",
    factor: float = 1.0,
) -> Signal:
    target = entry + (entry - stop) * 2 if direction == "LONG" else entry - (stop - entry) * 2
    return Signal(
        symbol="TEST",
        direction=direction,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        confidence=0.8,
        score=0.7,
        regime="bull",
        timestamp=int(time.time()),
        position_size_factor=factor,
    )


def _make_account(
    cash: float = 100_000.0,
    bp: float = 100_000.0,
) -> AccountState:
    return AccountState(
        net_liquidation=cash,
        total_cash_value=cash,
        buying_power=bp,
        available_funds=cash,
        excess_liquidity=cash * 0.5,
        init_margin_req=0.0,
        maint_margin_req=0.0,
        sma=cash,
        day_trades_remaining=-1,
    )


DEFAULT_RISK = RiskConfig(strategy_capital=10_000, risk_per_trade_pct=1.0)


class TestPositionSizer:
    def setup_method(self) -> None:
        self.sizer = PositionSizer(risk_config=DEFAULT_RISK)

    def test_happy_path_long(self) -> None:
        # $100 entry, $98 stop -> distance $2, risk $100 -> 50 shares
        sig = _make_signal(entry=100.0, stop=98.0)
        result = self.sizer.calculate(sig, _make_account(), {})
        assert result == 50

    def test_happy_path_short(self) -> None:
        # $50 entry, $52 stop -> distance $2, risk $100 -> 50 shares
        sig = _make_signal(entry=50.0, stop=52.0, direction="SHORT")
        result = self.sizer.calculate(sig, _make_account(), {})
        assert result == 50

    def test_volume_conviction_scaling(self) -> None:
        # factor=0.5 halves shares: 50 * 0.5 = 25
        sig = _make_signal(entry=100.0, stop=98.0, factor=0.5)
        result = self.sizer.calculate(sig, _make_account(), {})
        assert result == 25

    def test_strategy_budget_cap(self) -> None:
        # Only $500 left in budget -> max 5 shares at $100
        positions = {
            "AAPL": Position(
                symbol="AAPL",
                direction="LONG",
                shares=95,
                entry_price=100.0,
                entry_time=0,
                current_price=100.0,
                stop_price=98.0,
                unrealized_pnl=0.0,
            )
        }
        sig = _make_signal(entry=100.0, stop=98.0)
        result = self.sizer.calculate(sig, _make_account(), positions)
        assert result == 5

    def test_cash_cap(self) -> None:
        # Cash = $200 -> max 2 shares at $100
        sig = _make_signal(entry=100.0, stop=98.0)
        result = self.sizer.calculate(sig, _make_account(cash=200.0), {})
        assert result == 2

    def test_buying_power_cap(self) -> None:
        # BP = $300, cash high -> max 3 shares at $100
        sig = _make_signal(entry=100.0, stop=98.0)
        result = self.sizer.calculate(sig, _make_account(cash=100_000.0, bp=300.0), {})
        assert result == 3

    def test_zero_distance_returns_zero(self) -> None:
        sig = _make_signal(entry=100.0, stop=98.0)
        # Override stop to equal entry (bypass validator)
        object.__setattr__(sig, "stop_price", 100.0)
        result = self.sizer.calculate(sig, _make_account(), {})
        assert result == 0

    def test_zero_entry_returns_zero(self) -> None:
        sig = _make_signal(entry=100.0, stop=98.0)
        object.__setattr__(sig, "entry_price", 0.0)
        result = self.sizer.calculate(sig, _make_account(), {})
        assert result == 0

    def test_floor_behavior(self) -> None:
        # budget remaining = $90, at $100/share -> 0.9 -> floor to 0
        positions = {
            "AAPL": Position(
                symbol="AAPL",
                direction="LONG",
                shares=9910,
                entry_price=1.0,
                entry_time=0,
                current_price=1.0,
                stop_price=0.5,
                unrealized_pnl=0.0,
            )
        }
        sig = _make_signal(entry=100.0, stop=98.0)
        result = self.sizer.calculate(sig, _make_account(), positions)
        assert result == 0

    def test_existing_positions_reduce_budget(self) -> None:
        # Deploy $5000 -> $5000 left -> budget_cap=50, formula=50 -> 50
        positions = {
            "AAPL": Position(
                symbol="AAPL",
                direction="LONG",
                shares=50,
                entry_price=100.0,
                entry_time=0,
                current_price=100.0,
                stop_price=98.0,
                unrealized_pnl=0.0,
            )
        }
        sig = _make_signal(entry=100.0, stop=98.0)
        result = self.sizer.calculate(sig, _make_account(), positions)
        assert result == 50

    def test_budget_fully_deployed_returns_zero(self) -> None:
        positions = {
            "AAPL": Position(
                symbol="AAPL",
                direction="LONG",
                shares=100,
                entry_price=100.0,
                entry_time=0,
                current_price=100.0,
                stop_price=98.0,
                unrealized_pnl=0.0,
            )
        }
        sig = _make_signal(entry=100.0, stop=98.0)
        result = self.sizer.calculate(sig, _make_account(), positions)
        assert result == 0

    def test_zero_cash_returns_zero(self) -> None:
        sig = _make_signal(entry=100.0, stop=98.0)
        result = self.sizer.calculate(sig, _make_account(cash=0.0, bp=100_000.0), {})
        assert result == 0

    def test_zero_buying_power_returns_zero(self) -> None:
        sig = _make_signal(entry=100.0, stop=98.0)
        result = self.sizer.calculate(sig, _make_account(cash=100_000.0, bp=0.0), {})
        assert result == 0
