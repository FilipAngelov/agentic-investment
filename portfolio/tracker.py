"""Real-time position tracking and P&L."""

from __future__ import annotations

from data.models import ClosedTrade, Position


def _compute_unrealized(pos: Position) -> float:
    if pos.direction == "LONG":
        return (pos.current_price - pos.entry_price) * pos.shares
    return (pos.entry_price - pos.current_price) * pos.shares


class PositionTracker:
    """Central registry of bot positions with real-time P&L."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._realized_pnl: float = 0.0
        self._closed_trades: list[ClosedTrade] = []

    # --- Queries --------------------------------------------------------

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def get(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def __len__(self) -> int:
        return len(self._positions)

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._positions

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def unrealized_pnl(self) -> float:
        return sum(_compute_unrealized(p) for p in self._positions.values())

    @property
    def total_pnl(self) -> float:
        return self._realized_pnl + self.unrealized_pnl

    @property
    def deployed_capital(self) -> float:
        return sum(p.shares * p.entry_price for p in self._positions.values())

    @property
    def closed_trades(self) -> list[ClosedTrade]:
        return list(self._closed_trades)

    # --- Mutations ------------------------------------------------------

    def open_position(
        self,
        symbol: str,
        direction: str,
        shares: int,
        entry_price: float,
        entry_time: int,
        stop_price: float,
        current_price: float,
        sector: str | None = None,
    ) -> Position:
        unrealized = (
            (current_price - entry_price) * shares
            if direction == "LONG"
            else (entry_price - current_price) * shares
        )
        pos = Position(
            symbol=symbol,
            direction=direction,  # type: ignore[arg-type]
            shares=shares,
            entry_price=entry_price,
            entry_time=entry_time,
            current_price=current_price,
            stop_price=stop_price,
            unrealized_pnl=unrealized,
            sector=sector,
        )
        self._positions[symbol] = pos
        return pos

    def update_price(self, symbol: str, current_price: float) -> None:
        pos = self._positions[symbol]  # KeyError if missing
        updated = pos.model_copy(
            update={
                "current_price": current_price,
                "unrealized_pnl": (
                    (current_price - pos.entry_price) * pos.shares
                    if pos.direction == "LONG"
                    else (pos.entry_price - current_price) * pos.shares
                ),
            }
        )
        self._positions[symbol] = updated

    def update_prices(self, prices: dict[str, float]) -> None:
        for symbol, price in prices.items():
            if symbol in self._positions:
                self.update_price(symbol, price)

    def update_stop(self, symbol: str, stop_price: float) -> None:
        pos = self._positions[symbol]
        self._positions[symbol] = pos.model_copy(update={"stop_price": stop_price})

    def reduce_shares(
        self, symbol: str, shares_sold: int, exit_price: float, exit_time: int
    ) -> float:
        pos = self._positions[symbol]
        if pos.direction == "LONG":
            pnl = (exit_price - pos.entry_price) * shares_sold
        else:
            pnl = (pos.entry_price - exit_price) * shares_sold

        entry_cost = pos.entry_price * shares_sold
        pnl_pct = (pnl / entry_cost) * 100 if entry_cost else 0.0

        self._closed_trades.append(
            ClosedTrade(
                symbol=symbol,
                direction=pos.direction,
                shares=shares_sold,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                entry_time=pos.entry_time,
                exit_time=exit_time,
                pnl=pnl,
                pnl_pct=pnl_pct,
            )
        )
        self._realized_pnl += pnl

        remaining = pos.shares - shares_sold
        if remaining <= 0:
            del self._positions[symbol]
        else:
            self._positions[symbol] = pos.model_copy(update={"shares": remaining})
        return pnl

    def close_position(self, symbol: str, exit_price: float, exit_time: int) -> float:
        pos = self._positions[symbol]
        return self.reduce_shares(symbol, pos.shares, exit_price, exit_time)

    def as_share_counts(self) -> dict[str, int]:
        return {sym: pos.shares for sym, pos in self._positions.items()}
