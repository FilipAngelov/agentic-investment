"""Tests for PartialExitManager (task 3.5)."""

from __future__ import annotations

import pytest

from data.models import Position
from execution.partial_exit import PartialExitManager


def _pos(
    symbol: str = "AAPL",
    direction: str = "LONG",
    shares: int = 100,
    entry_price: float = 100.0,
) -> Position:
    return Position(
        symbol=symbol,
        direction=direction,
        shares=shares,
        entry_price=entry_price,
        entry_time=0,
        current_price=entry_price,
        stop_price=entry_price - 5.0 if direction == "LONG" else entry_price + 5.0,
        unrealized_pnl=0.0,
    )


class TestPartialExitManager:
    def test_no_profit_returns_none(self) -> None:
        mgr = PartialExitManager()
        sig = mgr.check(_pos(), current_price=100.0, atr=2.0)
        assert sig is None

    def test_tier1_long(self) -> None:
        mgr = PartialExitManager()
        sig = mgr.check(_pos(), current_price=102.4, atr=2.0)  # +1.2 ATR
        assert sig is not None
        assert sig.tier == 1
        assert sig.shares_to_sell == 25
        assert sig.profit_atr == pytest.approx(1.2)

    def test_tier1_before_tier2(self) -> None:
        """Even if profit > 2 ATR, tier 1 fires first."""
        mgr = PartialExitManager()
        sig = mgr.check(_pos(), current_price=105.0, atr=2.0)  # +2.5 ATR
        assert sig is not None
        assert sig.tier == 1

    def test_tier_already_taken(self) -> None:
        mgr = PartialExitManager()
        mgr.check(_pos(), current_price=102.4, atr=2.0)  # take tier 1
        sig = mgr.check(_pos(), current_price=102.4, atr=2.0)
        assert sig is None  # still +1.2 ATR, tier 1 already taken, tier 2 not reached

    def test_short_mirror(self) -> None:
        mgr = PartialExitManager()
        pos = _pos(direction="SHORT", entry_price=100.0)
        sig = mgr.check(pos, current_price=97.6, atr=2.0)  # +1.2 ATR short profit
        assert sig is not None
        assert sig.tier == 1
        assert sig.direction == "SHORT"
        assert sig.profit_atr == pytest.approx(1.2)

    def test_both_tiers_in_sequence(self) -> None:
        mgr = PartialExitManager()
        pos = _pos()
        sig1 = mgr.check(pos, current_price=102.4, atr=2.0)
        assert sig1 is not None and sig1.tier == 1

        # Now price at +2.5 ATR → tier 2
        sig2 = mgr.check(pos, current_price=105.0, atr=2.0)
        assert sig2 is not None and sig2.tier == 2
        assert sig2.shares_to_sell == 25

    def test_reset_clears_tiers(self) -> None:
        mgr = PartialExitManager()
        mgr.check(_pos(), current_price=102.4, atr=2.0)
        mgr.reset("AAPL")
        sig = mgr.check(_pos(), current_price=102.4, atr=2.0)
        assert sig is not None and sig.tier == 1

    def test_small_position_returns_none(self) -> None:
        mgr = PartialExitManager()
        sig = mgr.check(_pos(shares=3), current_price=102.4, atr=2.0)
        assert sig is None

    def test_zero_atr_returns_none(self) -> None:
        mgr = PartialExitManager()
        sig = mgr.check(_pos(), current_price=105.0, atr=0.0)
        assert sig is None

    def test_check_all_batch(self) -> None:
        mgr = PartialExitManager()
        positions = {
            "AAPL": _pos("AAPL", entry_price=100.0),
            "MSFT": _pos("MSFT", entry_price=200.0, shares=50),
            "GOOG": _pos("GOOG", entry_price=150.0),
        }
        prices = {"AAPL": 104.0, "MSFT": 200.0, "GOOG": 155.0}
        atrs = {"AAPL": 2.0, "MSFT": 3.0, "GOOG": 4.0}
        signals = mgr.check_all(positions, prices, atrs)
        # AAPL: +2 ATR → tier 1, MSFT: 0 → none, GOOG: +1.25 ATR → tier 1
        assert len(signals) == 2
        syms = {s.symbol for s in signals}
        assert syms == {"AAPL", "GOOG"}
