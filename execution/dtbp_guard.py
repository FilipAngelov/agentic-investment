"""DTBP compliance: pre-trade checks, margin impact, protected shares."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from config.settings import RiskConfig, load_protected_positions
from data.models import (
    AccountState,
    Position,
    PreTradeResult,
    ProposedOrder,
    ReconciliationResult,
)


class ProtectedPositionError(Exception):
    """Raised when a sell order would touch protected shares."""


class ProtectedPositionsGuard:
    """Enforces sell protection on Filip's long-term holdings.

    Rule: before ANY sell order, compute sellable_shares = current - protected.
    If sellable_shares <= 0, block the sell entirely.
    """

    def __init__(self) -> None:
        self._protected: dict[str, int] = load_protected_positions()

    @property
    def protected(self) -> dict[str, int]:
        return dict(self._protected)

    def protected_shares(self, symbol: str) -> int:
        """Return number of protected shares for a symbol."""
        return self._protected.get(symbol.upper(), 0)

    def sellable_shares(self, symbol: str, current_position: int) -> int:
        """Calculate how many shares can be sold.

        sellable = current_position - protected_shares
        Never negative.
        """
        protected = self.protected_shares(symbol)
        return max(0, current_position - protected)

    def check_sell(self, symbol: str, current_position: int, sell_qty: int) -> None:
        """Validate a sell order against protected positions.

        Raises ProtectedPositionError if the sell would touch protected shares.
        """
        symbol = symbol.upper()
        sellable = self.sellable_shares(symbol, current_position)

        if sell_qty <= 0:
            raise ValueError(f"sell_qty must be positive, got {sell_qty}")

        if sellable == 0:
            raise ProtectedPositionError(
                f"Cannot sell {symbol}: all {current_position} shares are protected"
            )

        if sell_qty > sellable:
            raise ProtectedPositionError(
                f"Cannot sell {sell_qty} shares of {symbol}: "
                f"only {sellable} sellable (current={current_position}, "
                f"protected={self.protected_shares(symbol)})"
            )

    def is_protected(self, symbol: str) -> bool:
        """Check if a symbol has any protected shares."""
        return symbol.upper() in self._protected


@runtime_checkable
class AccountStateProvider(Protocol):
    """Protocol for fetching account state from broker."""

    async def get_account_state(self) -> AccountState: ...
    async def get_positions(self) -> dict[str, int]: ...


class DTBPGuard:
    """Full pre-trade compliance gate.

    Composes ProtectedPositionsGuard and adds DTBP, cash-only, heat,
    drawdown, and day-trade checks.
    """

    def __init__(self, risk_config: RiskConfig | None = None) -> None:
        self._risk = risk_config or RiskConfig()
        self._protected = ProtectedPositionsGuard()

    @property
    def protected_guard(self) -> ProtectedPositionsGuard:
        return self._protected

    def pre_trade_check(
        self,
        order: ProposedOrder,
        account: AccountState,
        bot_positions: dict[str, Position],
    ) -> PreTradeResult:
        """Run all pre-trade checks in order, short-circuit on first failure."""
        passed: list[str] = []
        is_opening = not order.is_closing

        # 1. Daily drawdown kill switch
        drawdown_limit = self._risk.strategy_capital * self._risk.max_daily_drawdown_pct / 100
        if account.daily_pnl <= -drawdown_limit:
            return PreTradeResult(
                approved=False,
                reason=f"Daily drawdown kill switch: PnL {account.daily_pnl:.2f} <= -{drawdown_limit:.2f}",
                checks_passed=passed,
                checks_failed=["daily_drawdown"],
            )
        passed.append("daily_drawdown")

        # 2. Max positions
        if is_opening and len(bot_positions) >= self._risk.max_concurrent_positions:
            return PreTradeResult(
                approved=False,
                reason=f"Max positions reached: {len(bot_positions)} >= {self._risk.max_concurrent_positions}",
                checks_passed=passed,
                checks_failed=["max_positions"],
            )
        passed.append("max_positions")

        # 3. Protected shares (sells only)
        if order.direction == "SHORT" or (order.direction == "LONG" and order.is_closing):
            # For closing longs / shorting, check protected shares
            pass  # Handled below
        if not is_opening and order.direction == "LONG":
            # Closing a long = selling
            symbol = order.symbol.upper()
            protected = self._protected.protected_shares(symbol)
            if protected > 0:
                # Need to know current position to check
                pos = bot_positions.get(symbol)
                current = pos.shares if pos else 0
                # Bot positions + protected = total held; bot can only sell bot shares
                sellable = current  # bot can sell all its own shares
                if order.shares > sellable:
                    return PreTradeResult(
                        approved=False,
                        reason=f"Sell would touch protected shares of {symbol}",
                        checks_passed=passed,
                        checks_failed=["protected_shares"],
                    )
        if order.direction == "SHORT" and not order.is_closing:
            symbol = order.symbol.upper()
            protected = self._protected.protected_shares(symbol)
            if protected > 0:
                return PreTradeResult(
                    approved=False,
                    reason=f"Cannot short {symbol}: has protected shares",
                    checks_passed=passed,
                    checks_failed=["protected_shares"],
                )
        passed.append("protected_shares")

        # 4. Strategy budget
        order_cost = order.shares * order.limit_price
        if is_opening:
            deployed = sum(
                p.shares * p.entry_price for p in bot_positions.values()
            )
            if deployed + order_cost > self._risk.strategy_capital:
                return PreTradeResult(
                    approved=False,
                    reason=f"Strategy budget exceeded: {deployed + order_cost:.2f} > {self._risk.strategy_capital:.2f}",
                    checks_passed=passed,
                    checks_failed=["strategy_budget"],
                )
        passed.append("strategy_budget")

        # 5. Cash-only
        if is_opening and order_cost > account.total_cash_value:
            return PreTradeResult(
                approved=False,
                reason=f"Cash-only violation: order cost {order_cost:.2f} > cash {account.total_cash_value:.2f}",
                checks_passed=passed,
                checks_failed=["cash_only"],
            )
        passed.append("cash_only")

        # 6. DTBP
        if order_cost > account.buying_power:
            return PreTradeResult(
                approved=False,
                reason=f"DTBP exceeded: order cost {order_cost:.2f} > buying power {account.buying_power:.2f}",
                checks_passed=passed,
                checks_failed=["dtbp"],
            )
        passed.append("dtbp")

        # 7. Excess liquidity
        if account.net_liquidation > 0:
            excess_ratio = account.excess_liquidity / account.net_liquidation
            min_ratio = self._risk.min_excess_liquidity_pct / 100
            if excess_ratio < min_ratio:
                return PreTradeResult(
                    approved=False,
                    reason=f"Excess liquidity too low: {excess_ratio:.4f} < {min_ratio:.4f}",
                    checks_passed=passed,
                    checks_failed=["excess_liquidity"],
                )
        passed.append("excess_liquidity")

        # 8. Day trades
        if (
            order.is_closing
            and order.opened_today
            and account.net_liquidation < 25_000
            and account.day_trades_remaining == 0
        ):
            return PreTradeResult(
                approved=False,
                reason="Day trade blocked: no day trades remaining and NLV < $25K",
                checks_passed=passed,
                checks_failed=["day_trades"],
            )
        passed.append("day_trades")

        # 9. Portfolio heat
        if is_opening and account.net_liquidation > 0:
            current_heat = self._calc_portfolio_heat(bot_positions, account.net_liquidation)
            order_heat = self._calc_order_heat(order, account.net_liquidation)
            max_heat = self._risk.max_portfolio_heat_pct / 100
            if current_heat + order_heat > max_heat:
                return PreTradeResult(
                    approved=False,
                    reason=f"Portfolio heat {current_heat + order_heat:.4f} > max {max_heat:.4f}",
                    checks_passed=passed,
                    checks_failed=["portfolio_heat"],
                )
        passed.append("portfolio_heat")

        # 10. Sector heat
        if is_opening and order.sector and account.net_liquidation > 0:
            sector_heat = self._calc_sector_heat(
                bot_positions, order.sector, account.net_liquidation
            )
            order_heat = self._calc_order_heat(order, account.net_liquidation)
            max_sector = self._risk.max_sector_heat_pct / 100
            if sector_heat + order_heat > max_sector:
                return PreTradeResult(
                    approved=False,
                    reason=f"Sector heat for {order.sector}: {sector_heat + order_heat:.4f} > max {max_sector:.4f}",
                    checks_passed=passed,
                    checks_failed=["sector_heat"],
                )
        passed.append("sector_heat")

        return PreTradeResult(approved=True, checks_passed=passed)

    def reconcile(
        self,
        bot_positions: dict[str, int],
        ibkr_positions: dict[str, int],
    ) -> ReconciliationResult:
        """Check bot + protected == IBKR for every symbol."""
        expected: dict[str, int] = {}
        # Start with protected
        for sym, shares in self._protected.protected.items():
            expected[sym] = expected.get(sym, 0) + shares
        # Add bot
        for sym, shares in bot_positions.items():
            expected[sym] = expected.get(sym, 0) + shares

        all_symbols = set(expected) | set(ibkr_positions)
        mismatches: list[str] = []
        for sym in sorted(all_symbols):
            exp = expected.get(sym, 0)
            actual = ibkr_positions.get(sym, 0)
            if exp != actual:
                mismatches.append(f"{sym}: expected={exp}, actual={actual}")

        return ReconciliationResult(matches=len(mismatches) == 0, mismatches=mismatches)

    def check_nlv_threshold(
        self, account: AccountState, threshold: float = 25_000
    ) -> str | None:
        """Return warning if NLV within 5% of threshold."""
        if account.net_liquidation < threshold:
            return f"NLV ${account.net_liquidation:,.2f} is below ${threshold:,.2f}"
        margin = (account.net_liquidation - threshold) / threshold
        if margin < 0.05:
            return f"NLV ${account.net_liquidation:,.2f} is within 5% of ${threshold:,.2f}"
        return None

    @staticmethod
    def _calc_order_heat(order: ProposedOrder, nlv: float) -> float:
        """Heat = |entry - stop| / entry × (shares × entry) / NLV."""
        entry = order.limit_price
        stop = order.stop_price
        if entry == 0 or nlv == 0:
            return 0.0
        return abs(entry - stop) / entry * (order.shares * entry) / nlv

    @staticmethod
    def _calc_portfolio_heat(positions: dict[str, Position], nlv: float) -> float:
        if nlv == 0:
            return 0.0
        total = 0.0
        for pos in positions.values():
            if pos.entry_price == 0:
                continue
            total += (
                abs(pos.entry_price - pos.stop_price)
                / pos.entry_price
                * (pos.shares * pos.entry_price)
                / nlv
            )
        return total

    @staticmethod
    def _calc_sector_heat(
        positions: dict[str, Position], sector: str, nlv: float
    ) -> float:
        if nlv == 0:
            return 0.0
        total = 0.0
        for pos in positions.values():
            if pos.sector != sector or pos.entry_price == 0:
                continue
            total += (
                abs(pos.entry_price - pos.stop_price)
                / pos.entry_price
                * (pos.shares * pos.entry_price)
                / nlv
            )
        return total
