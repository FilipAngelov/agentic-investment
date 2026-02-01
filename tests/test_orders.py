"""Tests for execution.orders — OrderManager bracket pipeline."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from config.settings import RiskConfig
from data.models import AccountState, OrderResult, Position, Signal
from execution.orders import OrderManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_risk() -> RiskConfig:
    return RiskConfig(
        strategy_capital=10_000,
        risk_per_trade_pct=1.0,
        max_daily_drawdown_pct=2.0,
        max_concurrent_positions=5,
        max_portfolio_heat_pct=6.0,
        max_sector_heat_pct=3.0,
        min_excess_liquidity_pct=5.0,
    )


def _make_account(**overrides) -> AccountState:
    defaults = dict(
        net_liquidation=100_000,
        total_cash_value=50_000,
        buying_power=200_000,
        available_funds=50_000,
        excess_liquidity=40_000,
        init_margin_req=10_000,
        maint_margin_req=8_000,
        sma=100_000,
        day_trades_remaining=-1,
        daily_pnl=0.0,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def _make_signal(direction="LONG", **overrides) -> Signal:
    defaults = dict(
        symbol="AAPL",
        direction=direction,
        entry_price=150.0,
        stop_price=148.0 if direction == "LONG" else 152.0,
        target_price=156.0 if direction == "LONG" else 144.0,
        confidence=0.8,
        score=0.75,
        regime="bull",
        timestamp=int(time.time()),
        atr=2.5,
    )
    defaults.update(overrides)
    return Signal(**defaults)


def _make_ib_mock() -> MagicMock:
    """Create a mock IB connection with auto-incrementing order IDs."""
    ib = MagicMock()
    _next_id = [1]

    def _get_req_id():
        oid = _next_id[0]
        _next_id[0] += 1
        return oid

    ib.client.getReqId.side_effect = _get_req_id

    def _place_order(contract, order):
        trade = MagicMock()
        trade.order = order
        return trade

    ib.placeOrder.side_effect = _place_order
    return ib


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("execution.orders.DTBPGuard.__init__", return_value=None)
@patch("execution.orders.DTBPGuard.pre_trade_check")
def test_happy_path_long(mock_guard_check, mock_guard_init):
    """LONG signal → sizes → guard approves → 3 bracket orders placed."""
    from data.models import PreTradeResult

    mock_guard_check.return_value = PreTradeResult(
        approved=True, checks_passed=["all"]
    )

    ib = _make_ib_mock()
    risk = _make_risk()
    mgr = OrderManager(ib, risk)
    # Re-assign guard since we patched __init__
    mgr._guard = MagicMock()
    mgr._guard.pre_trade_check.return_value = PreTradeResult(
        approved=True, checks_passed=["all"]
    )

    signal = _make_signal("LONG")
    account = _make_account()
    result = mgr.execute_signal(signal, account, {})

    assert result.success is True
    assert result.symbol == "AAPL"
    assert result.direction == "LONG"
    assert result.shares > 0
    assert result.entry_price == 150.0
    assert result.stop_price == 148.0
    assert result.target_price == 156.0
    assert len(result.order_ids) == 3
    assert ib.placeOrder.call_count == 3

    # Verify entry action is BUY
    calls = ib.placeOrder.call_args_list
    parent_order = calls[0][0][1]
    assert parent_order.action == "BUY"
    # Exit orders are SELL
    stop_order = calls[1][0][1]
    target_order = calls[2][0][1]
    assert stop_order.action == "SELL"
    assert target_order.action == "SELL"


@patch("execution.orders.DTBPGuard.__init__", return_value=None)
def test_happy_path_short(mock_guard_init):
    """SHORT signal → correct action reversal (SELL entry, BUY stop/target)."""
    from data.models import PreTradeResult

    ib = _make_ib_mock()
    risk = _make_risk()
    mgr = OrderManager(ib, risk)
    mgr._guard = MagicMock()
    mgr._guard.pre_trade_check.return_value = PreTradeResult(
        approved=True, checks_passed=["all"]
    )

    signal = _make_signal("SHORT")
    account = _make_account()
    result = mgr.execute_signal(signal, account, {})

    assert result.success is True
    assert result.direction == "SHORT"

    calls = ib.placeOrder.call_args_list
    parent_order = calls[0][0][1]
    assert parent_order.action == "SELL"
    stop_order = calls[1][0][1]
    target_order = calls[2][0][1]
    assert stop_order.action == "BUY"
    assert target_order.action == "BUY"


@patch("execution.orders.DTBPGuard.__init__", return_value=None)
def test_sizing_returns_zero(mock_guard_init):
    """When sizer returns 0, skip — no orders placed."""
    ib = _make_ib_mock()
    risk = _make_risk()
    mgr = OrderManager(ib, risk)

    # Account with no cash → sizer returns 0
    account = _make_account(total_cash_value=0, buying_power=0)
    signal = _make_signal("LONG")
    result = mgr.execute_signal(signal, account, {})

    assert result.success is False
    assert result.shares == 0
    assert "0 shares" in result.reason
    assert ib.placeOrder.call_count == 0


@patch("execution.orders.DTBPGuard.__init__", return_value=None)
def test_guard_rejects(mock_guard_init):
    """When guard rejects, no orders placed, reason propagated."""
    from data.models import PreTradeResult

    ib = _make_ib_mock()
    risk = _make_risk()
    mgr = OrderManager(ib, risk)
    mgr._guard = MagicMock()
    mgr._guard.pre_trade_check.return_value = PreTradeResult(
        approved=False,
        reason="DTBP exceeded",
        checks_failed=["dtbp"],
    )

    signal = _make_signal("LONG")
    account = _make_account()
    result = mgr.execute_signal(signal, account, {})

    assert result.success is False
    assert result.reason == "DTBP exceeded"
    assert result.pre_trade is not None
    assert not result.pre_trade.approved
    assert ib.placeOrder.call_count == 0


@patch("execution.orders.DTBPGuard.__init__", return_value=None)
def test_order_ids_parent_child_linkage(mock_guard_init):
    """Parent ID is set on children."""
    from data.models import PreTradeResult

    ib = _make_ib_mock()
    risk = _make_risk()
    mgr = OrderManager(ib, risk)
    mgr._guard = MagicMock()
    mgr._guard.pre_trade_check.return_value = PreTradeResult(
        approved=True, checks_passed=["all"]
    )

    signal = _make_signal("LONG")
    account = _make_account()
    result = mgr.execute_signal(signal, account, {})

    calls = ib.placeOrder.call_args_list
    parent_order = calls[0][0][1]
    stop_order = calls[1][0][1]
    target_order = calls[2][0][1]

    parent_id = parent_order.orderId
    assert stop_order.parentId == parent_id
    assert target_order.parentId == parent_id


@patch("execution.orders.DTBPGuard.__init__", return_value=None)
def test_transmit_flags(mock_guard_init):
    """Parent transmit=False, stop transmit=False, target transmit=True."""
    from data.models import PreTradeResult

    ib = _make_ib_mock()
    risk = _make_risk()
    mgr = OrderManager(ib, risk)
    mgr._guard = MagicMock()
    mgr._guard.pre_trade_check.return_value = PreTradeResult(
        approved=True, checks_passed=["all"]
    )

    signal = _make_signal("LONG")
    account = _make_account()
    mgr.execute_signal(signal, account, {})

    calls = ib.placeOrder.call_args_list
    parent_order = calls[0][0][1]
    stop_order = calls[1][0][1]
    target_order = calls[2][0][1]

    assert parent_order.transmit is False
    assert stop_order.transmit is False
    assert target_order.transmit is True


def test_cancel_order():
    """cancel_order delegates to ib.cancelOrder."""
    ib = MagicMock()
    risk = _make_risk()
    # Bypass DTBPGuard loading protected positions
    with patch("execution.orders.DTBPGuard.__init__", return_value=None):
        mgr = OrderManager(ib, risk)

    mgr.cancel_order(42)
    ib.cancelOrder.assert_called_once()
    order_arg = ib.cancelOrder.call_args[0][0]
    assert order_arg.orderId == 42
