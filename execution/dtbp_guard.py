"""DTBP compliance: pre-trade checks, margin impact, protected shares."""

from config.settings import load_protected_positions


class ProtectedPositionError(Exception):
    """Raised when a sell order would touch protected shares."""


class ProtectedPositionsGuard:
    """Enforces sell protection on Filip's long-term holdings.

    Rule: before ANY sell order, compute sellable_shares = current - protected.
    If sellable_shares <= 0, block the sell entirely.
    """

    def __init__(self) -> None:
        self._protected: dict[str, int] = load_protected_positions()

    @property
    def protected(self) -> dict[str, int]:
        return dict(self._protected)

    def protected_shares(self, symbol: str) -> int:
        """Return number of protected shares for a symbol."""
        return self._protected.get(symbol.upper(), 0)

    def sellable_shares(self, symbol: str, current_position: int) -> int:
        """Calculate how many shares can be sold.

        sellable = current_position - protected_shares
        Never negative.
        """
        protected = self.protected_shares(symbol)
        return max(0, current_position - protected)

    def check_sell(self, symbol: str, current_position: int, sell_qty: int) -> None:
        """Validate a sell order against protected positions.

        Raises ProtectedPositionError if the sell would touch protected shares.
        """
        symbol = symbol.upper()
        sellable = self.sellable_shares(symbol, current_position)

        if sell_qty <= 0:
            raise ValueError(f"sell_qty must be positive, got {sell_qty}")

        if sellable == 0:
            raise ProtectedPositionError(
                f"Cannot sell {symbol}: all {current_position} shares are protected"
            )

        if sell_qty > sellable:
            raise ProtectedPositionError(
                f"Cannot sell {sell_qty} shares of {symbol}: "
                f"only {sellable} sellable (current={current_position}, "
                f"protected={self.protected_shares(symbol)})"
            )

    def is_protected(self, symbol: str) -> bool:
        """Check if a symbol has any protected shares."""
        return symbol.upper() in self._protected
