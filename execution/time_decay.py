"""Exit or tighten stops on positions that aren't working within expected timeframes."""

from __future__ import annotations

from config.settings import RiskConfig
from data.models import Position, TimeDecaySignal


class TimeDecayManager:
    """Exits or tightens stops on stale positions."""

    def __init__(self, risk_config: RiskConfig | None = None) -> None:
        self._risk = risk_config or RiskConfig()

    def check(
        self,
        position: Position,
        current_price: float,
        atr: float,
        now_ts: int,
    ) -> TimeDecaySignal | None:
        """Evaluate time decay rules for a single position."""
        if atr <= 0:
            return None

        hours_held = (now_ts - position.entry_time) / 3600.0

        if position.direction == "LONG":
            profit_atr = (current_price - position.entry_price) / atr
        else:
            profit_atr = (position.entry_price - current_price) / atr

        max_hours = self._risk.time_decay_max_days * 24
        stall_hours = self._risk.time_decay_days * 24
        thesis_hours = self._risk.time_decay_hours

        # Rule 1: max hold exceeded
        if hours_held >= max_hours:
            return TimeDecaySignal(
                symbol=position.symbol,
                direction=position.direction,
                action="exit",
                reason="max_hold_exceeded",
                hours_held=round(hours_held, 2),
                profit_atr=round(profit_atr, 4),
            )

        # Rule 2: stalled momentum
        if hours_held >= stall_hours and profit_atr < 1.0:
            return TimeDecaySignal(
                symbol=position.symbol,
                direction=position.direction,
                action="exit",
                reason="stalled_momentum",
                hours_held=round(hours_held, 2),
                profit_atr=round(profit_atr, 4),
            )

        # Rule 3: thesis not confirmed — tighten to breakeven
        if hours_held >= thesis_hours and profit_atr < 0.5:
            if position.direction == "LONG":
                new_stop = max(position.entry_price, position.stop_price)
            else:
                new_stop = min(position.entry_price, position.stop_price)

            # If stop is already at/beyond breakeven, nothing to do
            if new_stop == position.stop_price:
                return None

            return TimeDecaySignal(
                symbol=position.symbol,
                direction=position.direction,
                action="tighten_stop",
                reason="thesis_not_confirmed",
                hours_held=round(hours_held, 2),
                profit_atr=round(profit_atr, 4),
                new_stop=round(new_stop, 4),
            )

        return None

    def check_all(
        self,
        positions: dict[str, Position],
        current_prices: dict[str, float],
        atrs: dict[str, float],
        now_ts: int,
    ) -> list[TimeDecaySignal]:
        """Batch check all positions."""
        signals: list[TimeDecaySignal] = []
        for sym, pos in positions.items():
            if sym not in current_prices or sym not in atrs:
                continue
            sig = self.check(pos, current_prices[sym], atrs[sym], now_ts)
            if sig is not None:
                signals.append(sig)
        return signals
