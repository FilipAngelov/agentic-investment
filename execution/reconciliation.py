"""Daily reconciliation: bot + protected == IBKR total.

Mismatch = halt trading + alert Filip (CLAUDE.md rule #8).
"""

from __future__ import annotations

import time

from data.models import ReconciliationSnapshot

_MAX_HISTORY = 100


class Reconciler:
    """Daily reconciliation: bot + protected == IBKR total.

    Wraps the reconcile logic with halt flag and audit trail.
    Mismatch = halt trading + alert Filip (CLAUDE.md rule #8).
    """

    def __init__(self, protected_positions: dict[str, int]) -> None:
        self._protected = protected_positions
        self._halted: bool = False
        self._history: list[ReconciliationSnapshot] = []

    @property
    def is_halted(self) -> bool:
        return self._halted

    def reset_halt(self) -> None:
        """Manual reset (new trading day)."""
        self._halted = False

    def run(
        self,
        bot_positions: dict[str, int],
        ibkr_positions: dict[str, int],
    ) -> ReconciliationSnapshot:
        """Run reconciliation. Halts on any mismatch."""
        # Build expected = protected + bot
        expected: dict[str, int] = {}
        for sym, shares in self._protected.items():
            expected[sym] = expected.get(sym, 0) + shares
        for sym, shares in bot_positions.items():
            expected[sym] = expected.get(sym, 0) + shares

        all_symbols = set(expected) | set(ibkr_positions)
        mismatches: list[str] = []
        for sym in sorted(all_symbols):
            exp = expected.get(sym, 0)
            actual = ibkr_positions.get(sym, 0)
            if exp != actual:
                mismatches.append(f"{sym}: expected={exp}, actual={actual}")

        matches = len(mismatches) == 0
        snapshot = ReconciliationSnapshot(
            timestamp=int(time.time()),
            matches=matches,
            mismatches=mismatches,
            bot_total_symbols=len(bot_positions),
            ibkr_total_symbols=len(ibkr_positions),
            protected_total_symbols=len(self._protected),
        )

        if not matches:
            self._halted = True

        self._history.append(snapshot)
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

        return snapshot

    @property
    def history(self) -> list[ReconciliationSnapshot]:
        return list(self._history)
