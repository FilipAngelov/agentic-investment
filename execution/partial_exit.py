"""Partial exit manager — scale out at ATR-based profit tiers."""

from __future__ import annotations

from config.settings import RiskConfig
from data.models import PartialExitSignal, Position


class PartialExitManager:
    """Generates partial exit signals at ATR profit tiers."""

    def __init__(self, risk_config: RiskConfig | None = None) -> None:
        self._risk = risk_config or RiskConfig()
        self._taken: dict[str, set[int]] = {}  # symbol → {tiers taken}

    # -- tier thresholds keyed by tier number --
    def _thresholds(self) -> dict[int, float]:
        return {
            1: self._risk.partial_exit_1_atr,
            2: self._risk.partial_exit_2_atr,
        }

    def reset(self, symbol: str) -> None:
        """Clear tier tracking for a symbol (after full exit)."""
        self._taken.pop(symbol, None)

    def check(
        self,
        position: Position,
        current_price: float,
        atr: float,
    ) -> PartialExitSignal | None:
        """Return a signal for the next untaken tier, or None."""
        if atr <= 0:
            return None

        # Profit in ATR units
        if position.direction == "LONG":
            profit_atr = (current_price - position.entry_price) / atr
        else:
            profit_atr = (position.entry_price - current_price) / atr

        taken = self._taken.get(position.symbol, set())
        thresholds = self._thresholds()

        for tier in sorted(thresholds):
            if tier in taken:
                continue
            if profit_atr >= thresholds[tier]:
                shares_to_sell = position.shares // 4
                if shares_to_sell < 1:
                    return None
                self._taken.setdefault(position.symbol, set()).add(tier)
                return PartialExitSignal(
                    symbol=position.symbol,
                    direction=position.direction,
                    tier=tier,
                    shares_to_sell=shares_to_sell,
                    profit_atr=profit_atr,
                    limit_price=current_price,
                )
            # Lower tier not yet reached → don't skip to higher
            break

        return None

    def check_all(
        self,
        positions: dict[str, Position],
        current_prices: dict[str, float],
        atrs: dict[str, float],
    ) -> list[PartialExitSignal]:
        """Batch check all positions. Returns signals for triggered tiers."""
        signals: list[PartialExitSignal] = []
        for sym, pos in positions.items():
            price = current_prices.get(sym)
            atr = atrs.get(sym)
            if price is None or atr is None:
                continue
            sig = self.check(pos, price, atr)
            if sig is not None:
                signals.append(sig)
        return signals
