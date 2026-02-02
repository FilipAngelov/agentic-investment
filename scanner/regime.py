"""Market regime detection using SPY/VIX-based classification."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timezone

import asyncpg

from config.sectors import (
    BENCHMARK_SPY,
    BENCHMARK_VIX,
    REGIME_BEAR,
    REGIME_BEAR_LONG_FACTOR,
    REGIME_BEAR_SHORT_FACTOR,
    REGIME_BULL,
    REGIME_CHOPPY,
    REGIME_CRISIS,
    REGIME_FACTORS,
    REGIME_STRONG_BULL,
)
from data.models import RegimeType


class RegimeDetector:
    """Classify market regime from SPY price action and VIX levels."""

    def __init__(self) -> None:
        self._spy_closes: list[float] = []
        self._vix_closes: list[float] = []
        self._timestamps: list[int] = []
        self._current_regime: RegimeType | None = None
        self._previous_regime: RegimeType | None = None

    async def fetch_market_data(self, ib, days: int = 65) -> None:
        """Fetch daily bars for SPY and VIX from IBKR."""
        from ib_async import Index, Stock

        for sym, contract in [
            (BENCHMARK_SPY, Stock(BENCHMARK_SPY, "SMART", "USD")),
            (BENCHMARK_VIX, Index(BENCHMARK_VIX, "CBOE", "USD")),
        ]:
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=f"{days} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
            )
            closes = [b.close for b in bars]
            timestamps = [
                int(b.date.timestamp()) if isinstance(b.date, datetime)
                else int(datetime(b.date.year, b.date.month, b.date.day, tzinfo=timezone.utc).timestamp())
                if isinstance(b.date, date)
                else int(b.date)
                for b in bars
            ]
            if sym == BENCHMARK_SPY:
                self._spy_closes = closes
                if not self._timestamps and timestamps:
                    self._timestamps = timestamps
            else:
                self._vix_closes = closes
            await asyncio.sleep(0.05)

    def ingest_spy(self, closes: list[float], timestamps: list[int]) -> None:
        """Inject SPY data directly (for testing)."""
        self._spy_closes = list(closes)
        if not self._timestamps:
            self._timestamps = list(timestamps)

    def ingest_vix(self, closes: list[float], timestamps: list[int]) -> None:
        """Inject VIX data directly (for testing)."""
        self._vix_closes = list(closes)
        if not self._timestamps:
            self._timestamps = list(timestamps)

    @staticmethod
    def compute_sma(prices: list[float], period: int) -> float | None:
        """Simple moving average of last `period` values."""
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    def classify_regime(self) -> RegimeType | None:
        """Classify current market regime. Updates _current_regime."""
        if len(self._spy_closes) < 50 or not self._vix_closes:
            return None

        spy = self._spy_closes[-1]
        vix = self._vix_closes[-1]
        sma20 = self.compute_sma(self._spy_closes, 20)
        sma50 = self.compute_sma(self._spy_closes, 50)

        if sma20 is None or sma50 is None:
            return None

        # Priority order — first match wins
        if vix > 35:
            regime: RegimeType = REGIME_CRISIS
        elif spy > sma20 > sma50 and vix < 15:
            regime = REGIME_STRONG_BULL
        elif spy > sma50 and vix < 20:
            regime = REGIME_BULL
        elif spy < sma50 and vix >= 25:
            regime = REGIME_BEAR
        else:
            regime = REGIME_CHOPPY

        self._previous_regime = self._current_regime
        self._current_regime = regime
        return regime

    def get_regime_factor(self, direction: str = "LONG") -> float:
        """Return regime factor for current regime."""
        if self._current_regime is None:
            return 1.0
        if self._current_regime == REGIME_BEAR:
            return REGIME_BEAR_LONG_FACTOR if direction == "LONG" else REGIME_BEAR_SHORT_FACTOR
        return REGIME_FACTORS.get(self._current_regime, 1.0)

    def detect_regime_change(self) -> dict | None:
        """Return transition dict if regime changed, else None."""
        if (
            self._current_regime is None
            or self._previous_regime is None
            or self._current_regime == self._previous_regime
        ):
            return None
        return {
            "from_regime": self._previous_regime,
            "to_regime": self._current_regime,
            "timestamp": int(time.time()),
        }

    async def log_regime(self, conn: asyncpg.Connection) -> None:
        """Persist current regime to regime_log table."""
        if self._current_regime is None:
            return
        ts = int(time.time())
        sma_data = self.get_spy_vs_sma()
        vix = self.get_vix()
        factor = self.get_regime_factor()
        await conn.execute(
            "INSERT INTO regime_log "
            "(timestamp, regime, spy_vs_20sma, spy_vs_50sma, vix, regime_factor) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            ts, self._current_regime, sma_data["spy_vs_20sma"], sma_data["spy_vs_50sma"], vix, factor,
        )

    def get_current_regime(self) -> RegimeType | None:
        return self._current_regime

    def get_spy_vs_sma(self) -> dict:
        """Return SPY price, SMAs, and ratios."""
        spy = self._spy_closes[-1] if self._spy_closes else 0.0
        sma20 = self.compute_sma(self._spy_closes, 20)
        sma50 = self.compute_sma(self._spy_closes, 50)
        return {
            "spy_price": spy,
            "sma_20": sma20,
            "sma_50": sma50,
            "spy_vs_20sma": spy / sma20 if sma20 else None,
            "spy_vs_50sma": spy / sma50 if sma50 else None,
        }

    def get_vix(self) -> float | None:
        return self._vix_closes[-1] if self._vix_closes else None


# ---------------------------------------------------------------------------
# Module-level wrappers
# ---------------------------------------------------------------------------


async def get_current_regime(detector: RegimeDetector) -> RegimeType | None:
    return detector.get_current_regime()


async def get_regime_factor(detector: RegimeDetector, direction: str = "LONG") -> float:
    return detector.get_regime_factor(direction)


async def detect_regime_change(detector: RegimeDetector) -> dict | None:
    return detector.detect_regime_change()
