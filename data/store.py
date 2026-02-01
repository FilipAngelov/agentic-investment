"""PostgreSQL persistence layer (asyncpg)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from config.settings import DATABASE_URL

# ---------------------------------------------------------------------------
# Schema (individual statements for asyncpg — no multi-statement execute)
# ---------------------------------------------------------------------------

SCHEMA_STATEMENTS: list[str] = [
    # Price data (OHLCV)
    """CREATE TABLE IF NOT EXISTS bars (
        id SERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        timestamp BIGINT NOT NULL,
        timeframe TEXT NOT NULL,
        open DOUBLE PRECISION NOT NULL,
        high DOUBLE PRECISION NOT NULL,
        low DOUBLE PRECISION NOT NULL,
        close DOUBLE PRECISION NOT NULL,
        volume BIGINT NOT NULL,
        vwap DOUBLE PRECISION,
        trade_count INTEGER,
        UNIQUE(symbol, timestamp, timeframe)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_bars_sym_ts ON bars(symbol, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_bars_ts ON bars(timestamp)",

    # Sector ETF tracking
    """CREATE TABLE IF NOT EXISTS sector_snapshots (
        id SERIAL PRIMARY KEY,
        timestamp BIGINT NOT NULL,
        sector TEXT NOT NULL,
        price DOUBLE PRECISION NOT NULL,
        change_pct DOUBLE PRECISION,
        volume BIGINT,
        relative_strength DOUBLE PRECISION,
        momentum_score DOUBLE PRECISION,
        UNIQUE(sector, timestamp)
    )""",

    # News & catalysts
    """CREATE TABLE IF NOT EXISTS catalysts (
        id SERIAL PRIMARY KEY,
        timestamp BIGINT NOT NULL,
        symbol TEXT,
        sector TEXT,
        headline TEXT NOT NULL,
        source TEXT NOT NULL,
        sentiment DOUBLE PRECISION,
        magnitude INTEGER,
        catalyst_type TEXT,
        raw_text TEXT,
        llm_analysis TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_catalysts_sym ON catalysts(symbol, timestamp)",

    # Trade log
    """CREATE TABLE IF NOT EXISTS trades (
        id SERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        entry_time BIGINT NOT NULL,
        entry_price DOUBLE PRECISION NOT NULL,
        entry_shares INTEGER NOT NULL,
        exit_time BIGINT,
        exit_price DOUBLE PRECISION,
        exit_shares INTEGER,
        pnl DOUBLE PRECISION,
        pnl_pct DOUBLE PRECISION,
        signal_score DOUBLE PRECISION,
        signal_reason TEXT,
        exit_reason TEXT,
        catalyst_id INTEGER REFERENCES catalysts(id),
        regime TEXT,
        sector TEXT
    )""",

    # Account snapshots (for equity curve)
    """CREATE TABLE IF NOT EXISTS account_snapshots (
        id SERIAL PRIMARY KEY,
        timestamp BIGINT NOT NULL,
        net_liquidation DOUBLE PRECISION NOT NULL,
        cash DOUBLE PRECISION,
        buying_power DOUBLE PRECISION,
        day_trades_remaining INTEGER,
        daily_pnl DOUBLE PRECISION,
        open_positions INTEGER,
        portfolio_heat DOUBLE PRECISION
    )""",

    # Market regime log
    """CREATE TABLE IF NOT EXISTS regime_log (
        id SERIAL PRIMARY KEY,
        timestamp BIGINT NOT NULL,
        regime TEXT NOT NULL,
        spy_vs_20sma DOUBLE PRECISION,
        spy_vs_50sma DOUBLE PRECISION,
        vix DOUBLE PRECISION,
        regime_factor DOUBLE PRECISION
    )""",
]

# ---------------------------------------------------------------------------
# Connection pool (lazy singleton)
# ---------------------------------------------------------------------------

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return (and lazily create) the global connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return _pool


@asynccontextmanager
async def get_db() -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection from the pool (async context manager)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


async def close_pool() -> None:
    """Close the global pool (call at shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create all tables and indexes if they don't exist."""
    async with get_db() as conn:
        async with conn.transaction():
            for stmt in SCHEMA_STATEMENTS:
                await conn.execute(stmt)


# ---------------------------------------------------------------------------
# Catalysts
# ---------------------------------------------------------------------------


async def insert_catalyst(
    conn: asyncpg.Connection,
    *,
    timestamp: int,
    symbol: str | None,
    sector: str | None,
    headline: str,
    source: str,
    sentiment: float | None,
    magnitude: int | None,
    catalyst_type: str | None,
    raw_text: str | None,
    llm_analysis: str | None,
) -> int:
    """Insert a catalyst row and return its id."""
    row_id: int = await conn.fetchval(
        """INSERT INTO catalysts
           (timestamp, symbol, sector, headline, source, sentiment, magnitude,
            catalyst_type, raw_text, llm_analysis)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
           RETURNING id""",
        timestamp, symbol, sector, headline, source, sentiment, magnitude,
        catalyst_type, raw_text, llm_analysis,
    )
    return row_id


async def query_catalysts(
    conn: asyncpg.Connection,
    *,
    since_ts: int,
    symbol: str | None = None,
) -> list[dict]:
    """Return catalyst rows newer than *since_ts*, optionally for a symbol."""
    if symbol:
        rows = await conn.fetch(
            "SELECT * FROM catalysts WHERE timestamp >= $1 AND symbol = $2 ORDER BY timestamp DESC",
            since_ts, symbol,
        )
    else:
        rows = await conn.fetch(
            "SELECT * FROM catalysts WHERE timestamp >= $1 ORDER BY timestamp DESC",
            since_ts,
        )
    return [dict(r) for r in rows]


async def headline_exists(conn: asyncpg.Connection, headline_hash: str) -> bool:
    """Check if a catalyst with this headline already exists (by exact headline match)."""
    row = await conn.fetchrow(
        "SELECT 1 FROM catalysts WHERE headline = $1 LIMIT 1",
        headline_hash,
    )
    return row is not None


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------


async def insert_bars(conn: asyncpg.Connection, bars: list[dict]) -> int:
    """Bulk upsert bars. Each bar is a dict with Bar model fields.

    Returns the number of rows inserted.
    """
    if not bars:
        return 0
    for bar in bars:
        await conn.execute(
            """INSERT INTO bars
               (symbol, timestamp, timeframe, open, high, low, close, volume, vwap, trade_count)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               ON CONFLICT (symbol, timestamp, timeframe)
               DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                             close = EXCLUDED.close, volume = EXCLUDED.volume,
                             vwap = EXCLUDED.vwap, trade_count = EXCLUDED.trade_count""",
            bar["symbol"], bar["timestamp"], bar["timeframe"],
            bar["open"], bar["high"], bar["low"], bar["close"],
            bar["volume"], bar.get("vwap"), bar.get("trade_count"),
        )
    return len(bars)


async def query_bars(
    conn: asyncpg.Connection,
    symbol: str,
    timeframe: str,
    since_ts: int | None = None,
    until_ts: int | None = None,
) -> list[dict]:
    """Return bar rows for a symbol/timeframe, optionally filtered by timestamp range."""
    clauses = ["symbol = $1", "timeframe = $2"]
    params: list = [symbol, timeframe]
    idx = 3
    if since_ts is not None:
        clauses.append(f"timestamp >= ${idx}")
        params.append(since_ts)
        idx += 1
    if until_ts is not None:
        clauses.append(f"timestamp <= ${idx}")
        params.append(until_ts)
        idx += 1
    where = " AND ".join(clauses)
    rows = await conn.fetch(
        f"SELECT * FROM bars WHERE {where} ORDER BY timestamp ASC",
        *params,
    )
    return [dict(r) for r in rows]


async def get_latest_bar_timestamp(
    conn: asyncpg.Connection, symbol: str, timeframe: str
) -> int | None:
    """Return the most recent bar timestamp for a symbol/timeframe, or None."""
    row = await conn.fetchval(
        "SELECT MAX(timestamp) FROM bars WHERE symbol = $1 AND timeframe = $2",
        symbol, timeframe,
    )
    return row


async def cleanup_old_bars(
    conn: asyncpg.Connection,
    now_ts: int,
    retention_1m_days: int = 5,
    retention_5m_days: int = 90,
) -> None:
    """Delete expired intraday bars based on retention policy."""
    secs_per_day = 86400
    cutoff_1m = now_ts - retention_1m_days * secs_per_day
    cutoff_5m = now_ts - retention_5m_days * secs_per_day
    await conn.execute(
        "DELETE FROM bars WHERE timeframe = '1m' AND timestamp < $1", cutoff_1m
    )
    await conn.execute(
        "DELETE FROM bars WHERE timeframe = '5m' AND timestamp < $1", cutoff_5m
    )


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


async def insert_trade(
    conn: asyncpg.Connection,
    *,
    symbol: str,
    direction: str,
    entry_time: int,
    entry_price: float,
    entry_shares: int,
    exit_time: int,
    exit_price: float,
    exit_shares: int,
    pnl: float,
    pnl_pct: float,
    signal_score: float | None,
    signal_reason: str | None,
    exit_reason: str | None,
    catalyst_id: int | None,
    regime: str | None,
    sector: str | None,
) -> int:
    """Insert a completed trade and return its id."""
    row_id: int = await conn.fetchval(
        """INSERT INTO trades
           (symbol, direction, entry_time, entry_price, entry_shares,
            exit_time, exit_price, exit_shares, pnl, pnl_pct,
            signal_score, signal_reason, exit_reason, catalyst_id, regime, sector)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
           RETURNING id""",
        symbol, direction, entry_time, entry_price, entry_shares,
        exit_time, exit_price, exit_shares, pnl, pnl_pct,
        signal_score, signal_reason, exit_reason, catalyst_id, regime, sector,
    )
    return row_id


async def query_trades(
    conn: asyncpg.Connection,
    *,
    since_ts: int | None = None,
    symbol: str | None = None,
    sector: str | None = None,
    regime: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Query trades with optional filters."""
    clauses: list[str] = []
    params: list = []
    idx = 1
    if since_ts is not None:
        clauses.append(f"exit_time >= ${idx}")
        params.append(since_ts)
        idx += 1
    if symbol is not None:
        clauses.append(f"symbol = ${idx}")
        params.append(symbol)
        idx += 1
    if sector is not None:
        clauses.append(f"sector = ${idx}")
        params.append(sector)
        idx += 1
    if regime is not None:
        clauses.append(f"regime = ${idx}")
        params.append(regime)
        idx += 1
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = await conn.fetch(
        f"SELECT * FROM trades{where} ORDER BY exit_time DESC LIMIT ${idx}",
        *params,
    )
    return [dict(r) for r in rows]


async def get_trade_summary(
    conn: asyncpg.Connection,
    *,
    since_ts: int,
) -> dict:
    """Return aggregate stats: count, total_pnl, win_count, loss_count."""
    row = await conn.fetchrow(
        """SELECT
               COUNT(*) AS count,
               COALESCE(SUM(pnl), 0.0) AS total_pnl,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS win_count,
               SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) AS loss_count
           FROM trades
           WHERE exit_time >= $1""",
        since_ts,
    )
    return {
        "count": row["count"],
        "total_pnl": float(row["total_pnl"]),
        "win_count": row["win_count"] or 0,
        "loss_count": row["loss_count"] or 0,
    }
