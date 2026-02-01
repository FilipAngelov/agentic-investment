"""Position sizing: risk-per-trade / distance-to-stop, capped by account constraints."""

from __future__ import annotations

from config.settings import RiskConfig
from data.models import AccountState, Position, Signal


class PositionSizer:
    """Calculate share count from Signal + AccountState.

    Formula:
        risk_amount = strategy_capital × risk_per_trade_pct / 100
        distance    = |entry_price - stop_price|
        base_shares = risk_amount / distance
        adjusted    = base_shares × signal.position_size_factor
        final       = floor(min(adjusted, budget_cap, cash_cap, bp_cap))
    """

    def __init__(self, risk_config: RiskConfig | None = None) -> None:
        self._risk = risk_config or RiskConfig()

    def calculate(
        self,
        signal: Signal,
        account: AccountState,
        bot_positions: dict[str, Position],
    ) -> int:
        """Return number of shares to trade. 0 = skip."""
        entry = signal.entry_price
        distance = self._distance_to_stop(signal)

        if entry <= 0 or distance <= 0:
            return 0

        # Core formula
        risk_amount = self._risk.strategy_capital * self._risk.risk_per_trade_pct / 100
        base_shares = risk_amount / distance
        adjusted = base_shares * signal.position_size_factor

        # Cap 1: strategy budget remaining
        deployed = sum(p.shares * p.entry_price for p in bot_positions.values())
        budget_remaining = self._risk.strategy_capital - deployed
        if budget_remaining <= 0:
            return 0
        budget_cap = budget_remaining / entry

        # Cap 2: available cash
        if account.total_cash_value <= 0:
            return 0
        cash_cap = account.total_cash_value / entry

        # Cap 3: buying power
        if account.buying_power <= 0:
            return 0
        bp_cap = account.buying_power / entry

        final = int(min(adjusted, budget_cap, cash_cap, bp_cap))
        return max(final, 0)

    @staticmethod
    def _distance_to_stop(signal: Signal) -> float:
        """Absolute distance between entry and stop."""
        return abs(signal.entry_price - signal.stop_price)
