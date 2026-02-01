"""Tests for data.ingest and related store helpers."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from data.ingest import (
    RateLimiter,
    _chunk_date_range,
    _ibkr_bars_to_models,
    fetch_bars,
    make_contract,
    sync_symbol,
)
from data.store import (
    cleanup_old_bars,
    get_latest_bar_timestamp,
    insert_bars,
    query_bars,
)

SCHEMA_SQL_BARS = """
CREATE TABLE IF NOT EXISTS bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    timeframe TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    vwap REAL,
    trade_count INTEGER,
    UNIQUE(symbol, timestamp, timeframe)
);
"""


@pytest.fixture
async def db(tmp_path):
    """In-memory SQLite with bars table."""
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(db_path)
    await conn.executescript(SCHEMA_SQL_BARS)
    await conn.commit()
    conn.row_factory = aiosqlite.Row
    yield conn
    await conn.close()


def _make_bar_dict(symbol="SPY", ts=1000000, tf="1d", price=100.0):
    return {
        "symbol": symbol,
        "timestamp": ts,
        "timeframe": tf,
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price + 0.5,
        "volume": 1000,
        "vwap": price,
        "trade_count": 50,
    }


def _make_fake_ibkr_bar(ts_epoch, price=100.0):
    return SimpleNamespace(
        date=datetime.fromtimestamp(ts_epoch, tz=timezone.utc),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price + 0.5,
        volume=1000,
        average=price,
        barCount=50,
    )


# ---------------------------------------------------------------------------
# make_contract
# ---------------------------------------------------------------------------


class TestMakeContract:
    def test_stock(self):
        c = make_contract("AAPL")
        assert c.symbol == "AAPL"
        assert c.exchange == "SMART"

    def test_vix_index(self):
        c = make_contract("VIX")
        assert c.symbol == "VIX"
        assert c.exchange == "CBOE"

    def test_vix_case_insensitive(self):
        c = make_contract("vix")
        assert c.symbol == "VIX"


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_within_budget(self):
        rl = RateLimiter(max_per_sec=10, pacing_delay=0.0)
        start = time.monotonic()
        for _ in range(5):
            await rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5  # should be nearly instant

    @pytest.mark.asyncio
    async def test_acquire_respects_limit(self):
        rl = RateLimiter(max_per_sec=3, pacing_delay=0.0)
        start = time.monotonic()
        for _ in range(5):
            await rl.acquire()
        elapsed = time.monotonic() - start
        # Should have waited ~1 second after first 3
        assert elapsed >= 0.8


# ---------------------------------------------------------------------------
# Store: insert_bars / query_bars / get_latest_bar_timestamp
# ---------------------------------------------------------------------------


class TestStoreBarCRUD:
    @pytest.mark.asyncio
    async def test_insert_and_query(self, db):
        bars = [_make_bar_dict(ts=1000 + i) for i in range(5)]
        count = await insert_bars(db, bars)
        assert count == 5

        rows = await query_bars(db, "SPY", "1d")
        assert len(rows) == 5

    @pytest.mark.asyncio
    async def test_dedup_on_insert(self, db):
        bar = _make_bar_dict(ts=1000)
        await insert_bars(db, [bar])
        bar2 = _make_bar_dict(ts=1000, price=200.0)
        await insert_bars(db, [bar2])

        rows = await query_bars(db, "SPY", "1d")
        assert len(rows) == 1
        assert rows[0]["close"] == 200.5  # updated

    @pytest.mark.asyncio
    async def test_query_with_range(self, db):
        bars = [_make_bar_dict(ts=t) for t in [100, 200, 300, 400]]
        await insert_bars(db, bars)

        rows = await query_bars(db, "SPY", "1d", since_ts=200, until_ts=300)
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_get_latest_bar_timestamp(self, db):
        assert await get_latest_bar_timestamp(db, "SPY", "1d") is None

        bars = [_make_bar_dict(ts=t) for t in [100, 200, 300]]
        await insert_bars(db, bars)

        latest = await get_latest_bar_timestamp(db, "SPY", "1d")
        assert latest == 300

    @pytest.mark.asyncio
    async def test_insert_empty_list(self, db):
        count = await insert_bars(db, [])
        assert count == 0


# ---------------------------------------------------------------------------
# Store: cleanup_old_bars
# ---------------------------------------------------------------------------


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_removes_old_1m(self, db):
        now = 1_000_000
        old_ts = now - 10 * 86400  # 10 days ago
        new_ts = now - 2 * 86400   # 2 days ago

        await insert_bars(db, [_make_bar_dict(ts=old_ts, tf="1m")])
        await insert_bars(db, [_make_bar_dict(ts=new_ts, tf="1m")])

        await cleanup_old_bars(db, now_ts=now, retention_1m_days=5)

        rows = await query_bars(db, "SPY", "1m")
        assert len(rows) == 1
        assert rows[0]["timestamp"] == new_ts

    @pytest.mark.asyncio
    async def test_cleanup_keeps_daily(self, db):
        now = 1_000_000
        old_ts = now - 10 * 86400
        await insert_bars(db, [_make_bar_dict(ts=old_ts, tf="1d")])

        await cleanup_old_bars(db, now_ts=now, retention_1m_days=5, retention_5m_days=90)

        rows = await query_bars(db, "SPY", "1d")
        assert len(rows) == 1  # daily bars not cleaned


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


class TestChunking:
    def test_single_chunk(self):
        end = datetime(2024, 1, 10, tzinfo=timezone.utc)
        start = datetime(2024, 1, 9, tzinfo=timezone.utc)
        chunks = _chunk_date_range(start, end, 86400)
        assert len(chunks) == 1

    def test_multiple_chunks(self):
        end = datetime(2024, 1, 10, tzinfo=timezone.utc)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        chunks = _chunk_date_range(start, end, 3 * 86400)
        assert len(chunks) >= 3

    def test_empty_range(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        chunks = _chunk_date_range(dt, dt, 86400)
        assert len(chunks) == 0


# ---------------------------------------------------------------------------
# _ibkr_bars_to_models
# ---------------------------------------------------------------------------


class TestBarConversion:
    def test_converts_correctly(self):
        fake = _make_fake_ibkr_bar(1_700_000_000, price=150.0)
        result = _ibkr_bars_to_models([fake], "AAPL", "1d")
        assert len(result) == 1
        bar = result[0]
        assert bar.symbol == "AAPL"
        assert bar.timeframe == "1d"
        assert bar.open == 150.0
        assert bar.high == 151.0
        assert bar.low == 149.0


# ---------------------------------------------------------------------------
# fetch_bars (mocked IBKR)
# ---------------------------------------------------------------------------


class TestFetchBars:
    @pytest.mark.asyncio
    async def test_fetch_bars_returns_bars(self):
        fake_bar = _make_fake_ibkr_bar(1_700_000_000)
        ib = AsyncMock()
        ib.reqHistoricalDataAsync = AsyncMock(return_value=[fake_bar])

        with patch("data.ingest._rate_limiter") as mock_rl:
            mock_rl.acquire = AsyncMock()
            bars = await fetch_bars(
                ib, "SPY", "1d",
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                datetime(2024, 1, 2, tzinfo=timezone.utc),
            )
        assert len(bars) >= 1
        assert bars[0].symbol == "SPY"


# ---------------------------------------------------------------------------
# sync_symbol (mocked)
# ---------------------------------------------------------------------------


class TestSyncSymbol:
    @pytest.mark.asyncio
    async def test_incremental_sync(self, db):
        """When DB has bars, sync should fetch from last timestamp."""
        # Insert an existing bar
        await insert_bars(db, [_make_bar_dict(ts=1_700_000_000)])

        fake_bar = _make_fake_ibkr_bar(1_700_100_000)
        ib = AsyncMock()
        ib.reqHistoricalDataAsync = AsyncMock(return_value=[fake_bar])

        with patch("data.ingest._rate_limiter") as mock_rl:
            mock_rl.acquire = AsyncMock()
            count = await sync_symbol(ib, db, "SPY", "1d", lookback_days=500)

        assert count >= 1
        # Verify the call used an endDateTime (it was called)
        assert ib.reqHistoricalDataAsync.called
