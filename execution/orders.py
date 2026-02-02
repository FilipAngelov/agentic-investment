"""Order management: Signal → size → guard → bracket order via ib_async."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ib_async import LimitOrder, Order, StopOrder, Stock

from config.settings import DRY_RUN, RiskConfig
from data.models import (
    AccountState,
    DirectionType,
    OrderResult,
    Position,
    PreTradeResult,
    ProposedOrder,
    Signal,
)
from execution.dtbp_guard import DTBPGuard
from execution.sizing import PositionSizer

if TYPE_CHECKING:
    from ib_async import IB, Trade as IBTrade

logger = logging.getLogger(__name__)


class OrderManager:
    """Takes a Signal through the full execution pipeline.

    Pipeline: Signal → PositionSizer → DTBPGuard → bracket order (ib_async).
    """

    def __init__(
        self,
        ib: IB,
        risk_config: RiskConfig | None = None,
    ) -> None:
        self._ib = ib
        self._risk = risk_config or RiskConfig()
        self._sizer = PositionSizer(self._risk)
        self._guard = DTBPGuard(self._risk)

    def execute_signal(
        self,
        signal: Signal,
        account: AccountState,
        bot_positions: dict[str, Position],
    ) -> OrderResult:
        """Signal → size → guard → bracket order. Returns OrderResult."""
        symbol = signal.symbol
        direction = signal.direction

        # 1. Size
        shares = self._sizer.calculate(signal, account, bot_positions)
        if shares == 0:
            return OrderResult(
                success=False,
                symbol=symbol,
                direction=direction,
                reason="Position sizer returned 0 shares",
            )

        # 2. Build ProposedOrder
        order = ProposedOrder(
            symbol=symbol,
            direction=direction,
            shares=shares,
            limit_price=signal.entry_price,
            stop_price=signal.stop_price,
            sector=signal.sector,
        )

        # 3. Guard
        pre_trade = self._guard.pre_trade_check(order, account, bot_positions)
        if not pre_trade.approved:
            return OrderResult(
                success=False,
                symbol=symbol,
                direction=direction,
                shares=shares,
                reason=pre_trade.reason,
                pre_trade=pre_trade,
            )

        # 4. Dry-run gate
        if DRY_RUN:
            logger.info(
                "DRY_RUN: would place %s %s %d shares @ %.2f  stop=%.2f  target=%.2f",
                direction, symbol, shares, signal.entry_price, signal.stop_price, signal.target_price,
            )
            return OrderResult(
                success=False, symbol=symbol, direction=direction, shares=shares,
                entry_price=signal.entry_price, stop_price=signal.stop_price,
                target_price=signal.target_price, order_ids=[], pre_trade=pre_trade,
                reason="DRY_RUN",
            )

        # 5. Place bracket
        parent, stop_child, target_child = self._build_bracket(
            symbol=symbol,
            direction=direction,
            shares=shares,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
        )

        order_ids = [
            parent.order.orderId,
            stop_child.order.orderId,
            target_child.order.orderId,
        ]

        logger.info(
            "Bracket placed for %s %s %d shares @ %.2f  stop=%.2f  target=%.2f  ids=%s",
            direction,
            symbol,
            shares,
            signal.entry_price,
            signal.stop_price,
            signal.target_price,
            order_ids,
        )

        return OrderResult(
            success=True,
            symbol=symbol,
            direction=direction,
            shares=shares,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            order_ids=order_ids,
            pre_trade=pre_trade,
        )

    def cancel_order(self, order_id: int) -> None:
        """Cancel a pending order by constructing an Order with the given ID."""
        order = Order(orderId=order_id)
        self._ib.cancelOrder(order)

    def _build_bracket(
        self,
        symbol: str,
        direction: DirectionType,
        shares: int,
        entry_price: float,
        stop_price: float,
        target_price: float,
    ) -> tuple[IBTrade, IBTrade, IBTrade]:
        """Create and place a bracket order via ib_async.

        Returns (parent_trade, stop_trade, target_trade).
        """
        contract = Stock(symbol, "SMART", "USD")

        entry_action = "BUY" if direction == "LONG" else "SELL"
        exit_action = "SELL" if direction == "LONG" else "BUY"

        parent_id = self._ib.client.getReqId()

        # Parent: limit order, don't transmit yet
        parent_order = LimitOrder(
            action=entry_action,
            totalQuantity=shares,
            lmtPrice=entry_price,
            orderId=parent_id,
            transmit=False,
        )

        # Stop-loss child
        stop_order = StopOrder(
            action=exit_action,
            totalQuantity=shares,
            stopPrice=stop_price,
            parentId=parent_id,
            transmit=False,
        )

        # Take-profit child — transmit=True triggers the whole bracket
        target_order = LimitOrder(
            action=exit_action,
            totalQuantity=shares,
            lmtPrice=target_price,
            parentId=parent_id,
            transmit=True,
        )

        parent_trade = self._ib.placeOrder(contract, parent_order)
        stop_trade = self._ib.placeOrder(contract, stop_order)
        target_trade = self._ib.placeOrder(contract, target_order)

        return parent_trade, stop_trade, target_trade
