"""Data ingestion: IBKR historical data, rate limiting, dedup."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from ib_async import Contract, Index, Stock

from config.settings import ingest_config
from data.models import Bar, TimeframeType
from data.store import get_latest_bar_timestamp, insert_bars

if TYPE_CHECKING:
    import asyncpg
    from ib_async import IB

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IBKR bar-size & duration mappings
# ---------------------------------------------------------------------------

BAR_SIZE_MAP: dict[TimeframeType, str] = {
    "1m": "1 min",
    "5m": "5 mins",
    "15m": "15 mins",
    "1d": "1 day",
}

# Max duration string IBKR allows per request for each bar size
MAX_DURATION_MAP: dict[TimeframeType, tuple[str, int]] = {
    "1m": ("1800 S", 1800),        # 30 min chunks
    "5m": ("1 D", 86400),           # 1 day chunks
    "15m": ("2 D", 2 * 86400),      # 2 day chunks
    "1d": ("1 Y", 365 * 86400),     # 1 year chunks
}


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Token-bucket style async rate limiter."""

    def __init__(self, max_per_sec: int = 45, pacing_delay: float = 0.05):
        self._max_per_sec = max_per_sec
        self._pacing_delay = pacing_delay
        self._lock = asyncio.Lock()
        self._timestamps: list[float] = []

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # Purge timestamps older than 1 second
            self._timestamps = [t for t in self._timestamps if now - t < 1.0]
            if len(self._timestamps) >= self._max_per_sec:
                sleep_time = 1.0 - (now - self._timestamps[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                self._timestamps = [
                    t for t in self._timestamps if time.monotonic() - t < 1.0
                ]
            self._timestamps.append(time.monotonic())
            if self._pacing_delay > 0:
                await asyncio.sleep(self._pacing_delay)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_contract(symbol: str) -> Contract:
    """Build an IBKR Contract. VIX uses Index, everything else uses Stock."""
    if symbol.upper() == "VIX":
        return Index("VIX", "CBOE", "USD")
    return Stock(symbol, "SMART", "USD")


def _ibkr_bars_to_models(
    bars: list, symbol: str, timeframe: TimeframeType
) -> list[Bar]:
    """Convert ib_async BarData objects to our Bar model."""
    result: list[Bar] = []
    for b in bars:
        ts = int(b.date.timestamp()) if hasattr(b.date, "timestamp") else int(b.date)
        result.append(
            Bar(
                symbol=symbol,
                timestamp=ts,
                timeframe=timeframe,
                low=float(b.low),
                high=float(b.high),
                open=float(b.open),
                close=float(b.close),
                volume=int(b.volume) if b.volume >= 0 else 0,
                vwap=float(b.average) if hasattr(b, "average") and b.average else None,
                trade_count=int(b.barCount) if hasattr(b, "barCount") and b.barCount else None,
            )
        )
    return result


def _chunk_date_range(
    start: datetime, end: datetime, chunk_seconds: int
) -> list[tuple[datetime, str]]:
    """Split a date range into (end_dt, duration_str) tuples for IBKR requests.

    IBKR reqHistoricalData takes an endDateTime + durationStr going backwards.
    We chunk from *end* backwards to *start*.
    """
    chunks: list[tuple[datetime, str]] = []
    cursor = end
    while cursor > start:
        chunk_start = max(start, cursor - timedelta(seconds=chunk_seconds))
        delta_secs = int((cursor - chunk_start).total_seconds())
        if delta_secs <= 0:
            break
        # Build duration string
        if delta_secs >= 86400:
            days = max(1, delta_secs // 86400)
            dur_str = f"{days} D"
        else:
            dur_str = f"{delta_secs} S"
        chunks.append((cursor, dur_str))
        cursor = chunk_start
    return chunks


# ---------------------------------------------------------------------------
# Core fetch
# ---------------------------------------------------------------------------

_rate_limiter = RateLimiter(
    max_per_sec=ingest_config.max_requests_per_sec,
    pacing_delay=ingest_config.pacing_delay_sec,
)


async def fetch_bars(
    ib: IB,
    symbol: str,
    timeframe: TimeframeType,
    start_date: datetime,
    end_date: datetime,
) -> list[Bar]:
    """Fetch historical bars from IBKR, chunking as needed."""
    bar_size = BAR_SIZE_MAP[timeframe]
    _, chunk_secs = MAX_DURATION_MAP[timeframe]
    contract = make_contract(symbol)

    chunks = _chunk_date_range(start_date, end_date, chunk_secs)
    all_bars: list[Bar] = []

    for end_dt, dur_str in chunks:
        await _rate_limiter.acquire()
        try:
            raw = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime=end_dt,
                durationStr=dur_str,
                barSizeSetting=bar_size,
                whatToShow="TRADES" if symbol.upper() != "VIX" else "CBOE",
                useRTH=True,
                formatDate=2,
            )
        except Exception:
            logger.exception("IBKR request failed: %s %s %s", symbol, timeframe, end_dt)
            raise
        if raw:
            all_bars.extend(_ibkr_bars_to_models(raw, symbol, timeframe))

    # Deduplicate by timestamp (keep last)
    seen: dict[int, Bar] = {}
    for bar in all_bars:
        seen[bar.timestamp] = bar
    return sorted(seen.values(), key=lambda b: b.timestamp)


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


async def sync_symbol(
    ib: IB,
    db: asyncpg.Connection,
    symbol: str,
    timeframe: TimeframeType,
    lookback_days: int,
) -> int:
    """Sync one symbol/timeframe. Returns number of new bars inserted."""
    now = datetime.now(timezone.utc)
    latest_ts = await get_latest_bar_timestamp(db, symbol, timeframe)

    if latest_ts is not None:
        start = datetime.fromtimestamp(latest_ts, tz=timezone.utc)
    else:
        start = now - timedelta(days=lookback_days)

    bars = await fetch_bars(ib, symbol, timeframe, start, now)
    if not bars:
        return 0

    bar_dicts = [b.model_dump() for b in bars]
    # Remove id field (auto-generated by DB)
    for d in bar_dicts:
        d.pop("id", None)
    count = await insert_bars(db, bar_dicts)
    logger.info("Synced %d bars for %s/%s", count, symbol, timeframe)
    return count


def _lookback_for_timeframe(timeframe: TimeframeType) -> int:
    """Return default lookback days for a timeframe."""
    if timeframe == "1d":
        return ingest_config.daily_lookback_days
    if timeframe == "5m":
        return ingest_config.intraday_5m_lookback_days
    if timeframe == "15m":
        return ingest_config.intraday_15m_lookback_days
    return ingest_config.retention_1m_days  # 1m


async def sync_universe(
    ib: IB,
    db: asyncpg.Connection,
    symbols: list[str],
    timeframes: list[TimeframeType] | None = None,
) -> dict[str, int]:
    """Sync all symbols × timeframes. Returns {symbol: total_bars_inserted}."""
    if timeframes is None:
        timeframes = ["1d"]
    results: dict[str, int] = {}
    for symbol in symbols:
        total = 0
        for tf in timeframes:
            lookback = _lookback_for_timeframe(tf)
            retries = 0
            while retries <= ingest_config.max_retries:
                try:
                    count = await sync_symbol(ib, db, symbol, tf, lookback)
                    total += count
                    break
                except Exception:
                    retries += 1
                    if retries > ingest_config.max_retries:
                        logger.error(
                            "Giving up on %s/%s after %d retries",
                            symbol, tf, ingest_config.max_retries,
                        )
                    else:
                        logger.warning(
                            "Retry %d/%d for %s/%s",
                            retries, ingest_config.max_retries, symbol, tf,
                        )
                        await asyncio.sleep(1.0 * retries)
        results[symbol] = total
    return results


async def run_cleanup(db: asyncpg.Connection) -> None:
    """Delete expired intraday bars per retention policy."""
    from data.store import cleanup_old_bars

    now_ts = int(datetime.now(timezone.utc).timestamp())
    await cleanup_old_bars(
        db,
        now_ts=now_ts,
        retention_1m_days=ingest_config.retention_1m_days,
        retention_5m_days=ingest_config.retention_5m_days,
    )
    logger.info("Cleanup complete")
