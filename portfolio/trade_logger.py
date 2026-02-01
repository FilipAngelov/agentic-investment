"""Trade logger — persists ClosedTrade records to SQLite with signal context."""

from __future__ import annotations

import json
from dataclasses import dataclass

import asyncpg

from data.models import ClosedTrade, Signal
from data.store import insert_trade


@dataclass(frozen=True, slots=True)
class EntryContext:
    signal_score: float
    signal_reason: str | None
    regime: str | None
    sector: str | None
    catalyst_id: int | None


class TradeLogger:
    """Persists trades to PostgreSQL with full signal + exit context."""

    def __init__(self) -> None:
        self._entry_context: dict[str, EntryContext] = {}

    def record_entry(self, signal: Signal, shares: int, catalyst_id: int | None = None) -> None:
        """Stash signal context when a position opens."""
        reason_json: str | None = None
        if signal.reason is not None:
            # If reason is already a JSON string, keep it; otherwise wrap it
            try:
                json.loads(signal.reason)
                reason_json = signal.reason
            except (json.JSONDecodeError, TypeError):
                reason_json = json.dumps(signal.reason)

        self._entry_context[signal.symbol] = EntryContext(
            signal_score=signal.score,
            signal_reason=reason_json,
            regime=signal.regime,
            sector=signal.sector,
            catalyst_id=catalyst_id,
        )

    async def record_exit(
        self,
        db: asyncpg.Connection,
        closed: ClosedTrade,
        exit_reason: str,
    ) -> int:
        """Merge ClosedTrade + stashed entry context → INSERT into trades table.

        Returns the trade row id.
        """
        ctx = self._entry_context.get(closed.symbol)
        return await insert_trade(
            db,
            symbol=closed.symbol,
            direction=closed.direction,
            entry_time=closed.entry_time,
            entry_price=closed.entry_price,
            entry_shares=closed.shares,
            exit_time=closed.exit_time,
            exit_price=closed.exit_price,
            exit_shares=closed.shares,
            pnl=closed.pnl,
            pnl_pct=closed.pnl_pct,
            signal_score=ctx.signal_score if ctx else None,
            signal_reason=ctx.signal_reason if ctx else None,
            exit_reason=exit_reason,
            catalyst_id=ctx.catalyst_id if ctx else None,
            regime=ctx.regime if ctx else None,
            sector=ctx.sector if ctx else None,
        )

    async def record_exits(
        self,
        db: asyncpg.Connection,
        closed_trades: list[ClosedTrade],
        exit_reason: str,
    ) -> list[int]:
        """Batch version for multiple partial exits in one cycle."""
        ids: list[int] = []
        for closed in closed_trades:
            row_id = await self.record_exit(db, closed, exit_reason)
            ids.append(row_id)
        return ids

    def clear_context(self, symbol: str) -> None:
        """Remove stashed entry context (called when position fully closed)."""
        self._entry_context.pop(symbol, None)
