"""Adaptive ATR-based trailing stop with regime adjustment."""

from __future__ import annotations

from config.sectors import (
    REGIME_BEAR,
    REGIME_BEAR_LONG_FACTOR,
    REGIME_BEAR_SHORT_FACTOR,
    REGIME_FACTORS,
)
from config.settings import RiskConfig
from data.models import DirectionType, Position, RegimeType, StopUpdate


class TrailingStopManager:
    """ATR-based trailing stop that tightens as profit grows."""

    def __init__(self, risk_config: RiskConfig | None = None) -> None:
        self._risk = risk_config or RiskConfig()

    # ------------------------------------------------------------------
    def _get_regime_factor(self, regime: RegimeType, direction: DirectionType) -> float:
        if regime == REGIME_BEAR:
            return REGIME_BEAR_LONG_FACTOR if direction == "LONG" else REGIME_BEAR_SHORT_FACTOR
        return REGIME_FACTORS.get(regime, 1.0)

    def _select_k(self, profit_atr: float) -> float:
        if profit_atr >= 3.0:
            return self._risk.stop_k4
        if profit_atr >= 2.0:
            return self._risk.stop_k3
        if profit_atr >= 1.0:
            return self._risk.stop_k2
        return self._risk.stop_k1

    # ------------------------------------------------------------------
    def update_stop(
        self,
        position: Position,
        current_price: float,
        atr: float,
        regime: RegimeType,
    ) -> StopUpdate:
        """Recalculate trailing stop for a position."""
        old_stop = position.stop_price

        if atr <= 0:
            return StopUpdate(
                symbol=position.symbol,
                old_stop=old_stop,
                new_stop=old_stop,
                moved=False,
                profit_atr=0.0,
                k_used=self._risk.stop_k1,
                regime_factor=1.0,
            )

        if position.direction == "LONG":
            profit_atr = (current_price - position.entry_price) / atr
        else:
            profit_atr = (position.entry_price - current_price) / atr

        k = self._select_k(profit_atr)
        rf = self._get_regime_factor(regime, position.direction)
        adjusted_k = k * rf

        if position.direction == "LONG":
            candidate = current_price - adjusted_k * atr
            new_stop = max(candidate, old_stop)
        else:
            candidate = current_price + adjusted_k * atr
            new_stop = min(candidate, old_stop)

        return StopUpdate(
            symbol=position.symbol,
            old_stop=old_stop,
            new_stop=round(new_stop, 4),
            moved=new_stop != old_stop,
            profit_atr=round(profit_atr, 4),
            k_used=k,
            regime_factor=rf,
        )

    # ------------------------------------------------------------------
    def update_all(
        self,
        positions: dict[str, Position],
        current_prices: dict[str, float],
        atrs: dict[str, float],
        regime: RegimeType,
    ) -> list[StopUpdate]:
        """Batch update all positions. Returns only moves."""
        results: list[StopUpdate] = []
        for sym, pos in positions.items():
            if sym not in current_prices or sym not in atrs:
                continue
            upd = self.update_stop(pos, current_prices[sym], atrs[sym], regime)
            if upd.moved:
                results.append(upd)
        return results
