"""Sector momentum tracker using ETF proxies."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiosqlite

from config.sectors import (
    BENCHMARK_SPY,
    ETF_TO_SECTOR,
    SECTOR_ETFS,
    SECTOR_ETF_SYMBOLS,
)
from data.models import SectorSnapshot


class SectorTracker:
    """Track sector ETF momentum and relative strength."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path
        self._history: dict[str, list[float]] = {}
        self._timestamps: list[int] = []
        self._latest_prices: dict[str, float] = {}
        self._latest_volumes: dict[str, int] = {}

    async def fetch_sector_bars(self, ib, days: int = 65) -> None:
        """Fetch daily bars for all sector ETFs + SPY from IBKR."""
        from ib_async import Stock

        symbols = SECTOR_ETF_SYMBOLS + [BENCHMARK_SPY]
        for sym in symbols:
            contract = Stock(sym, "SMART", "USD")
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=f"{days} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
            )
            closes = [b.close for b in bars]
            timestamps = [int(b.date.timestamp()) for b in bars]
            self._history[sym] = closes
            if not self._timestamps and timestamps:
                self._timestamps = timestamps
            if closes:
                self._latest_prices[sym] = closes[-1]
            if bars:
                self._latest_volumes[sym] = int(bars[-1].volume)
            await asyncio.sleep(0.05)

    def ingest_bars(
        self, symbol: str, closes: list[float], timestamps: list[int]
    ) -> None:
        """Inject bar data directly (for testing)."""
        self._history[symbol] = list(closes)
        if not self._timestamps:
            self._timestamps = list(timestamps)
        if closes:
            self._latest_prices[symbol] = closes[-1]
            self._latest_volumes[symbol] = 0

    def compute_momentum(self, symbol: str) -> float | None:
        """Composite momentum: 0.5*ROC5 + 0.3*ROC20 + 0.2*ROC60."""
        closes = self._history.get(symbol)
        if not closes or len(closes) < 5:
            return None

        def roc(n: int) -> float | None:
            if len(closes) < n + 1 or closes[-(n + 1)] == 0:
                return None
            return (closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)]

        roc5 = roc(5)
        if roc5 is None:
            return None

        roc20 = roc(20)
        roc60 = roc(60)

        score = 0.5 * roc5
        if roc20 is not None:
            score += 0.3 * roc20
        if roc60 is not None:
            score += 0.2 * roc60

        return score

    def compute_relative_strength(self, symbol: str) -> float | None:
        """Sector 20d return / SPY 20d return."""
        closes = self._history.get(symbol)
        spy = self._history.get(BENCHMARK_SPY)
        if not closes or not spy:
            return None
        if len(closes) < 21 or len(spy) < 21:
            return None
        if closes[-21] == 0 or spy[-21] == 0:
            return None

        sector_ret = (closes[-1] - closes[-21]) / closes[-21]
        spy_ret = (spy[-1] - spy[-21]) / spy[-21]
        if spy_ret == 0:
            return None
        return sector_ret / spy_ret

    def get_sector_rankings(self) -> list[dict]:
        """All 11 sectors sorted by momentum descending."""
        rows: list[dict] = []
        for sector, etf in SECTOR_ETFS.items():
            closes = self._history.get(etf, [])
            price = self._latest_prices.get(etf, 0.0)
            change_pct = None
            if len(closes) >= 2 and closes[-2] != 0:
                change_pct = (closes[-1] - closes[-2]) / closes[-2]
            mom = self.compute_momentum(etf)
            rs = self.compute_relative_strength(etf)
            rows.append(
                {
                    "sector": sector,
                    "etf": etf,
                    "price": price,
                    "change_pct": change_pct,
                    "momentum_score": mom,
                    "relative_strength": rs,
                }
            )
        rows.sort(key=lambda r: r["momentum_score"] or float("-inf"), reverse=True)
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        return rows

    def get_sector_momentum(self, sector_name: str) -> dict | None:
        """Single sector lookup by name."""
        rankings = self.get_sector_rankings()
        for r in rankings:
            if r["sector"] == sector_name:
                return r
        return None

    def detect_rotation(self) -> list[dict]:
        """Compare 5d vs 20d momentum rankings to detect rotation."""
        # Build 5d and 20d rankings
        scores_5d: list[tuple[str, float]] = []
        scores_20d: list[tuple[str, float]] = []

        for sector, etf in SECTOR_ETFS.items():
            closes = self._history.get(etf, [])
            if len(closes) >= 6 and closes[-6] != 0:
                roc5 = (closes[-1] - closes[-6]) / closes[-6]
                scores_5d.append((sector, roc5))
            if len(closes) >= 21 and closes[-21] != 0:
                roc20 = (closes[-1] - closes[-21]) / closes[-21]
                scores_20d.append((sector, roc20))

        if not scores_5d or not scores_20d:
            return []

        scores_5d.sort(key=lambda x: x[1], reverse=True)
        scores_20d.sort(key=lambda x: x[1], reverse=True)

        rank_5d = {s: i for i, (s, _) in enumerate(scores_5d, 1)}
        rank_20d = {s: i for i, (s, _) in enumerate(scores_20d, 1)}

        rotations: list[dict] = []
        for sector in rank_5d:
            if sector not in rank_20d:
                continue
            diff = rank_20d[sector] - rank_5d[sector]
            if abs(diff) >= 3:
                rotations.append(
                    {
                        "sector": sector,
                        "rank_5d": rank_5d[sector],
                        "rank_20d": rank_20d[sector],
                        "direction": "inflow" if diff > 0 else "outflow",
                    }
                )
        return rotations

    def is_sector_accelerating(self, sector_name: str) -> bool:
        """True if ROC_5 > ROC_20 for the sector."""
        etf = SECTOR_ETFS.get(sector_name)
        if not etf:
            return False
        closes = self._history.get(etf, [])
        if len(closes) < 21:
            return False
        if closes[-6] == 0 or closes[-21] == 0:
            return False
        roc5 = (closes[-1] - closes[-6]) / closes[-6]
        roc20 = (closes[-1] - closes[-21]) / closes[-21]
        return roc5 > roc20

    async def save_snapshots(self, db: aiosqlite.Connection) -> None:
        """Persist current sector data as SectorSnapshot rows."""
        ts = int(time.time())
        for sector, etf in SECTOR_ETFS.items():
            closes = self._history.get(etf, [])
            price = self._latest_prices.get(etf, 0.0)
            change_pct = None
            if len(closes) >= 2 and closes[-2] != 0:
                change_pct = (closes[-1] - closes[-2]) / closes[-2]
            mom = self.compute_momentum(etf)
            rs = self.compute_relative_strength(etf)
            vol = self._latest_volumes.get(etf)
            await db.execute(
                "INSERT OR REPLACE INTO sector_snapshots "
                "(timestamp, sector, price, change_pct, volume, relative_strength, momentum_score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, sector, price, change_pct, vol, rs, mom),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# Module-level wrappers
# ---------------------------------------------------------------------------


async def get_sector_rankings(tracker: SectorTracker) -> list[dict]:
    return tracker.get_sector_rankings()


async def get_sector_momentum(
    tracker: SectorTracker, sector: str
) -> dict | None:
    return tracker.get_sector_momentum(sector)


def is_sector_accelerating(tracker: SectorTracker, sector: str) -> bool:
    return tracker.is_sector_accelerating(sector)
