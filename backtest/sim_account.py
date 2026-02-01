"""Simulated account state for backtesting — no IB connection needed."""

from __future__ import annotations

from data.models import AccountState, Position


class SimulatedAccount:
    """Cash-only simulated account that mirrors AccountState for DTBPGuard/Sizer."""

    def __init__(self, initial_capital: float) -> None:
        self._initial_capital = initial_capital
        self._cash = initial_capital
        self._total_commissions = 0.0
        self._daily_pnl = 0.0
        self._day_start_equity = initial_capital

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def total_commissions(self) -> float:
        return self._total_commissions

    def equity(self, positions: dict[str, Position]) -> float:
        deployed = sum(p.shares * p.current_price for p in positions.values())
        return self._cash + deployed

    def account_state(self, positions: dict[str, Position]) -> AccountState:
        """Build an AccountState compatible with DTBPGuard/Sizer."""
        eq = self.equity(positions)
        return AccountState(
            net_liquidation=eq,
            total_cash_value=self._cash,
            buying_power=self._cash,
            available_funds=self._cash,
            excess_liquidity=self._cash,
            init_margin_req=0.0,
            maint_margin_req=0.0,
            sma=eq,
            day_trades_remaining=-1,  # unlimited (simulating >$25K)
            daily_pnl=self._daily_pnl,
        )

    def on_fill_buy(self, shares: int, price: float, commission: float) -> None:
        cost = shares * price + commission
        self._cash -= cost
        self._total_commissions += commission
        self._daily_pnl -= commission

    def on_fill_sell(self, shares: int, price: float, commission: float, pnl: float) -> None:
        proceeds = shares * price - commission
        self._cash += proceeds
        self._total_commissions += commission
        self._daily_pnl += pnl - commission

    def new_day(self, positions: dict[str, Position]) -> None:
        self._day_start_equity = self.equity(positions)
        self._daily_pnl = 0.0
