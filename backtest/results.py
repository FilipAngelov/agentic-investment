"""Backtest results: equity curve, trade log, metrics computation."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

from portfolio.analytics import compute_metrics


@dataclass
class EquitySnapshot:
    timestamp: int
    equity: float
    cash: float
    positions_count: int
    daily_pnl: float


@dataclass
class TradeRecord:
    symbol: str
    direction: str
    shares: int
    entry_price: float
    exit_price: float
    entry_time: int
    exit_time: int
    pnl: float
    pnl_pct: float
    commission: float
    sector: str | None = None


class BacktestResults:
    """Collect snapshots and trades, compute final metrics."""

    def __init__(self, initial_capital: float) -> None:
        self._initial_capital = initial_capital
        self._snapshots: list[EquitySnapshot] = []
        self._trades: list[TradeRecord] = []
        self._total_commissions = 0.0

    def snapshot(
        self,
        ts: int,
        equity: float,
        cash: float,
        positions_count: int,
        daily_pnl: float,
    ) -> None:
        self._snapshots.append(EquitySnapshot(ts, equity, cash, positions_count, daily_pnl))

    def add_trade(self, trade: TradeRecord) -> None:
        self._trades.append(trade)
        self._total_commissions += trade.commission

    def compute(self) -> dict:
        """Compute performance metrics using portfolio/analytics."""
        trade_dicts = [
            {
                "pnl": t.pnl,
                "exit_time": t.exit_time,
                "symbol": t.symbol,
                "sector": t.sector,
                "direction": t.direction,
            }
            for t in self._trades
        ]
        return compute_metrics(trade_dicts, self._initial_capital)

    def summary(self) -> str:
        """Formatted text summary."""
        metrics = self.compute()
        final_eq = self._snapshots[-1].equity if self._snapshots else self._initial_capital
        lines = [
            "=" * 60,
            "BACKTEST RESULTS",
            "=" * 60,
            f"Initial capital:     ${self._initial_capital:>12,.2f}",
            f"Final equity:        ${final_eq:>12,.2f}",
            f"Total P&L:           ${metrics['total_pnl']:>12,.2f}",
            f"Total return:        {metrics['total_return_pct']:>11.2f}%",
            f"Max drawdown:        {metrics['max_drawdown_pct']:>11.2f}%",
            f"Total commissions:   ${self._total_commissions:>12,.2f}",
            "",
            f"Trades:              {metrics['count']:>12d}",
            f"Win rate:            {metrics['win_rate'] * 100:>11.1f}%",
            f"Profit factor:       {metrics['profit_factor']:>12.2f}",
            f"Avg win:             ${metrics['avg_win']:>12,.2f}",
            f"Avg loss:            ${metrics['avg_loss']:>12,.2f}",
            f"Expectancy:          ${metrics['expectancy']:>12,.2f}",
            "",
            f"Sharpe ratio:        {metrics['sharpe_ratio']:>12.2f}",
            f"Sortino ratio:       {metrics['sortino_ratio']:>12.2f}",
            f"Calmar ratio:        {metrics['calmar_ratio']:>12.2f}",
            f"Max consec. losses:  {metrics['max_consecutive_losses']:>12d}",
            f"Trades/day:          {metrics['trades_per_day']:>12.2f}",
            "=" * 60,
        ]
        return "\n".join(lines)

    def equity_curve_csv(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "equity", "cash", "positions", "daily_pnl"])
            for s in self._snapshots:
                w.writerow([s.timestamp, f"{s.equity:.2f}", f"{s.cash:.2f}", s.positions_count, f"{s.daily_pnl:.2f}"])

    def trades_csv(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "symbol", "direction", "shares", "entry_price", "exit_price",
                "entry_time", "exit_time", "pnl", "pnl_pct", "commission", "sector",
            ])
            for t in self._trades:
                w.writerow([
                    t.symbol, t.direction, t.shares, f"{t.entry_price:.4f}",
                    f"{t.exit_price:.4f}", t.entry_time, t.exit_time,
                    f"{t.pnl:.2f}", f"{t.pnl_pct:.2f}", f"{t.commission:.4f}",
                    t.sector or "",
                ])
