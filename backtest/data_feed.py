"""Historical data feed: load bars from PostgreSQL and replay chronologically."""

from __future__ import annotations

import bisect
from collections import defaultdict

import asyncpg


class HistoricalDataFeed:
    """In-memory bar store loaded from PostgreSQL for backtesting.

    Bars are indexed by (symbol, timeframe) and sorted by timestamp.
    Strict no-look-ahead: get_bars_up_to() only returns bars with ts <= current_ts.
    """

    def __init__(self) -> None:
        # (symbol, timeframe) -> list of bar dicts sorted by timestamp
        self._bars: dict[tuple[str, str], list[dict]] = defaultdict(list)
        # (symbol, timeframe) -> sorted list of timestamps (for bisect)
        self._timestamps: dict[tuple[str, str], list[int]] = defaultdict(list)
        # timeframe -> sorted unique timestamps
        self._tf_timestamps: dict[str, list[int]] = defaultdict(list)
        self._symbols: set[str] = set()

    async def load(
        self,
        conn: asyncpg.Connection,
        symbols: list[str],
        timeframes: list[str],
        start_ts: int,
        end_ts: int,
    ) -> None:
        """Bulk-load bars from the database."""
        for symbol in symbols:
            for tf in timeframes:
                rows = await conn.fetch(
                    "SELECT * FROM bars WHERE symbol = $1 AND timeframe = $2 "
                    "AND timestamp >= $3 AND timestamp <= $4 ORDER BY timestamp ASC",
                    symbol, tf, start_ts, end_ts,
                )
                bars = [dict(r) for r in rows]
                if not bars:
                    continue
                key = (symbol, tf)
                self._bars[key] = bars
                ts_list = [b["timestamp"] for b in bars]
                self._timestamps[key] = ts_list
                self._symbols.add(symbol)

        # Build per-timeframe timestamp index
        for (sym, tf), ts_list in self._timestamps.items():
            existing = set(self._tf_timestamps.get(tf, []))
            existing.update(ts_list)
            self._tf_timestamps[tf] = sorted(existing)

    def load_bars_direct(self, bars: list[dict]) -> None:
        """Load bars directly (for testing without DB)."""
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for b in bars:
            key = (b["symbol"], b["timeframe"])
            grouped[key].append(b)

        for key, bar_list in grouped.items():
            bar_list.sort(key=lambda b: b["timestamp"])
            self._bars[key] = bar_list
            self._timestamps[key] = [b["timestamp"] for b in bar_list]
            self._symbols.add(key[0])

        self._tf_timestamps.clear()
        for (sym, tf), ts_list in self._timestamps.items():
            existing = set(self._tf_timestamps.get(tf, []))
            existing.update(ts_list)
            self._tf_timestamps[tf] = sorted(existing)

    def daily_timestamps(self) -> list[int]:
        """Return sorted unique daily bar timestamps."""
        return self._tf_timestamps.get("1d", [])

    def intraday_timestamps(self, day_ts: int, timeframe: str) -> list[int]:
        """Return intraday timestamps for a given day.

        day_ts is the daily bar timestamp. We return all intraday timestamps
        within [day_ts, day_ts + 86400).
        """
        all_ts = self._tf_timestamps.get(timeframe, [])
        if not all_ts:
            return []
        start_idx = bisect.bisect_left(all_ts, day_ts)
        end_ts = day_ts + 86400
        end_idx = bisect.bisect_left(all_ts, end_ts)
        return all_ts[start_idx:end_idx]

    def get_bars_up_to(
        self,
        symbol: str,
        timeframe: str,
        current_ts: int,
        lookback: int = 200,
    ) -> list[dict]:
        """Return up to `lookback` bars with timestamp <= current_ts. No look-ahead."""
        key = (symbol, timeframe)
        ts_list = self._timestamps.get(key)
        if not ts_list:
            return []
        # bisect_right gives the index after the last ts <= current_ts
        right = bisect.bisect_right(ts_list, current_ts)
        if right == 0:
            return []
        left = max(0, right - lookback)
        return self._bars[key][left:right]

    def get_bar_at(self, symbol: str, timeframe: str, ts: int) -> dict | None:
        """Return the exact bar at timestamp ts, or None."""
        key = (symbol, timeframe)
        ts_list = self._timestamps.get(key)
        if not ts_list:
            return None
        idx = bisect.bisect_left(ts_list, ts)
        if idx < len(ts_list) and ts_list[idx] == ts:
            return self._bars[key][idx]
        return None

    def symbols_at(self, timeframe: str, ts: int) -> list[str]:
        """Return symbols that have a bar at exactly this timestamp."""
        result = []
        for (sym, tf), ts_list in self._timestamps.items():
            if tf != timeframe:
                continue
            idx = bisect.bisect_left(ts_list, ts)
            if idx < len(ts_list) and ts_list[idx] == ts:
                result.append(sym)
        return result

    def all_symbols(self) -> set[str]:
        return set(self._symbols)
