"""Realistic fill simulation: slippage, commissions, partial fills."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class FillResult:
    filled: bool
    fill_price: float
    fill_shares: int
    commission: float
    slippage_cost: float


class FillModel:
    """Simulates realistic order fills against OHLCV bars."""

    MIN_COMMISSION = 0.35

    def __init__(
        self,
        slippage_bps: float = 5.0,
        commission_per_share: float = 0.005,
        partial_fill_prob: float = 0.05,
        seed: int | None = None,
    ) -> None:
        self._slippage_bps = slippage_bps
        self._commission_per_share = commission_per_share
        self._partial_fill_prob = partial_fill_prob
        self._rng = random.Random(seed)

    def _commission(self, shares: int) -> float:
        return max(shares * self._commission_per_share, self.MIN_COMMISSION)

    def _apply_partial(self, shares: int) -> int:
        if self._rng.random() < self._partial_fill_prob:
            frac = self._rng.uniform(0.5, 0.9)
            partial = max(1, int(shares * frac))
            return partial
        return shares

    def _slippage(self, price: float, adverse: bool) -> float:
        """Return slippage amount. adverse=True means price moves against us."""
        slip = price * self._slippage_bps / 10_000
        return slip if adverse else -slip

    def try_entry_fill(
        self,
        limit_price: float,
        direction: str,
        shares: int,
        bar: dict,
    ) -> FillResult:
        """Simulate entry limit order fill against a bar.

        For LONG: fills if bar low <= limit_price. Fill at limit + slippage.
        For SHORT: fills if bar high >= limit_price. Fill at limit - slippage.
        """
        low, high = bar["low"], bar["high"]

        if direction == "LONG":
            if low > limit_price:
                return FillResult(False, 0.0, 0, 0.0, 0.0)
            slip = self._slippage(limit_price, adverse=True)
            fill_price = limit_price + slip
            # Cap at bar high
            fill_price = min(fill_price, high)
        else:
            if high < limit_price:
                return FillResult(False, 0.0, 0, 0.0, 0.0)
            slip = self._slippage(limit_price, adverse=True)
            fill_price = limit_price - slip
            fill_price = max(fill_price, low)

        fill_shares = self._apply_partial(shares)
        commission = self._commission(fill_shares)
        slippage_cost = abs(fill_price - limit_price) * fill_shares

        return FillResult(
            filled=True,
            fill_price=round(fill_price, 4),
            fill_shares=fill_shares,
            commission=round(commission, 4),
            slippage_cost=round(slippage_cost, 4),
        )

    def try_stop_fill(
        self,
        stop_price: float,
        direction: str,
        shares: int,
        bar: dict,
    ) -> FillResult:
        """Simulate stop order fill.

        For LONG stop (sell): fills if bar low <= stop_price.
        Gap-through: if bar opens below stop, fill at open.
        For SHORT stop (buy-to-cover): fills if bar high >= stop_price.
        """
        low, high, open_ = bar["low"], bar["high"], bar["open"]

        if direction == "LONG":
            # Stop-loss on a long = sell when price drops to stop
            if low > stop_price:
                return FillResult(False, 0.0, 0, 0.0, 0.0)
            # Gap-through: open below stop
            if open_ <= stop_price:
                fill_price = open_
            else:
                slip = self._slippage(stop_price, adverse=True)
                fill_price = stop_price - slip  # adverse = lower fill for long exit
                fill_price = max(fill_price, low)
        else:
            # Stop-loss on a short = buy when price rises to stop
            if high < stop_price:
                return FillResult(False, 0.0, 0, 0.0, 0.0)
            if open_ >= stop_price:
                fill_price = open_
            else:
                slip = self._slippage(stop_price, adverse=True)
                fill_price = stop_price + slip
                fill_price = min(fill_price, high)

        fill_shares = shares  # stops always fill fully
        commission = self._commission(fill_shares)
        slippage_cost = abs(fill_price - stop_price) * fill_shares

        return FillResult(
            filled=True,
            fill_price=round(fill_price, 4),
            fill_shares=fill_shares,
            commission=round(commission, 4),
            slippage_cost=round(slippage_cost, 4),
        )

    def try_target_fill(
        self,
        target_price: float,
        direction: str,
        shares: int,
        bar: dict,
    ) -> FillResult:
        """Simulate target (take-profit) order fill.

        For LONG: fills if bar high >= target. Fill at target - slippage (conservative).
        For SHORT: fills if bar low <= target. Fill at target + slippage (conservative).
        """
        low, high = bar["low"], bar["high"]

        if direction == "LONG":
            if high < target_price:
                return FillResult(False, 0.0, 0, 0.0, 0.0)
            slip = self._slippage(target_price, adverse=False)
            fill_price = target_price + slip  # slip is negative for favorable
            fill_price = max(fill_price, low)
        else:
            if low > target_price:
                return FillResult(False, 0.0, 0, 0.0, 0.0)
            slip = self._slippage(target_price, adverse=False)
            fill_price = target_price - slip
            fill_price = min(fill_price, high)

        fill_shares = self._apply_partial(shares)
        commission = self._commission(fill_shares)
        slippage_cost = abs(fill_price - target_price) * fill_shares

        return FillResult(
            filled=True,
            fill_price=round(fill_price, 4),
            fill_shares=fill_shares,
            commission=round(commission, 4),
            slippage_cost=round(slippage_cost, 4),
        )
