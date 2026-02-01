"""Tests for DTBP compliance module."""

from pathlib import Path
from textwrap import dedent

import pytest

from execution.dtbp_guard import ProtectedPositionError, ProtectedPositionsGuard


@pytest.fixture
def guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProtectedPositionsGuard:
    """Create a guard with a test CSV."""
    csv_file = tmp_path / "portfolio.csv"
    csv_file.write_text(dedent("""\
        symbol,shares,note
        NVDA,5,long-term hold
        AAPL,10,long-term hold
        ASML,1,long-term hold
    """))
    monkeypatch.setattr(
        "execution.dtbp_guard.load_protected_positions",
        lambda: {
            "NVDA": 5,
            "AAPL": 10,
            "ASML": 1,
        },
    )
    return ProtectedPositionsGuard()


class TestProtectedPositionsGuard:
    def test_protected_shares(self, guard: ProtectedPositionsGuard):
        assert guard.protected_shares("NVDA") == 5
        assert guard.protected_shares("AAPL") == 10
        assert guard.protected_shares("UNKNOWN") == 0

    def test_case_insensitive(self, guard: ProtectedPositionsGuard):
        assert guard.protected_shares("nvda") == 5
        assert guard.protected_shares("Aapl") == 10

    def test_sellable_shares_unprotected_symbol(self, guard: ProtectedPositionsGuard):
        # Symbol not in protected list — all shares sellable
        assert guard.sellable_shares("TSLA", 100) == 100

    def test_sellable_shares_protected_symbol(self, guard: ProtectedPositionsGuard):
        # Own 20 NVDA, 5 protected → 15 sellable
        assert guard.sellable_shares("NVDA", 20) == 15

    def test_sellable_shares_exactly_protected(self, guard: ProtectedPositionsGuard):
        # Own exactly the protected amount → 0 sellable
        assert guard.sellable_shares("NVDA", 5) == 0

    def test_sellable_shares_less_than_protected(self, guard: ProtectedPositionsGuard):
        # Somehow own fewer than protected (shouldn't happen, but guard against it)
        assert guard.sellable_shares("NVDA", 3) == 0

    def test_check_sell_allowed(self, guard: ProtectedPositionsGuard):
        # 20 NVDA, 5 protected, sell 10 → ok
        guard.check_sell("NVDA", current_position=20, sell_qty=10)

    def test_check_sell_max_sellable(self, guard: ProtectedPositionsGuard):
        # Sell exactly the sellable amount
        guard.check_sell("NVDA", current_position=20, sell_qty=15)

    def test_check_sell_blocked_all_protected(self, guard: ProtectedPositionsGuard):
        with pytest.raises(ProtectedPositionError, match="all 5 shares are protected"):
            guard.check_sell("NVDA", current_position=5, sell_qty=1)

    def test_check_sell_blocked_exceeds_sellable(self, guard: ProtectedPositionsGuard):
        with pytest.raises(ProtectedPositionError, match="only 15 sellable"):
            guard.check_sell("NVDA", current_position=20, sell_qty=16)

    def test_check_sell_unprotected_symbol(self, guard: ProtectedPositionsGuard):
        # Unprotected symbol — any sell is fine
        guard.check_sell("TSLA", current_position=100, sell_qty=100)

    def test_check_sell_invalid_qty(self, guard: ProtectedPositionsGuard):
        with pytest.raises(ValueError, match="sell_qty must be positive"):
            guard.check_sell("NVDA", current_position=20, sell_qty=0)

    def test_is_protected(self, guard: ProtectedPositionsGuard):
        assert guard.is_protected("NVDA") is True
        assert guard.is_protected("TSLA") is False

    def test_bot_bought_shares_of_protected_symbol(self, guard: ProtectedPositionsGuard):
        """If bot buys 50 ASML (Filip holds 1), can only sell 50, not 51."""
        guard.check_sell("ASML", current_position=51, sell_qty=50)
        with pytest.raises(ProtectedPositionError):
            guard.check_sell("ASML", current_position=51, sell_qty=51)
