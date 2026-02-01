"""SQLite persistence layer (aiosqlite)."""

import aiosqlite

from config.settings import DB_PATH

SCHEMA_SQL = """
-- Price data (OHLCV)
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
CREATE INDEX IF NOT EXISTS idx_bars_sym_ts ON bars(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_bars_ts ON bars(timestamp);

-- Sector ETF tracking
CREATE TABLE IF NOT EXISTS sector_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    sector TEXT NOT NULL,
    price REAL NOT NULL,
    change_pct REAL,
    volume INTEGER,
    relative_strength REAL,
    momentum_score REAL,
    UNIQUE(sector, timestamp)
);

-- News & catalysts
CREATE TABLE IF NOT EXISTS catalysts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    symbol TEXT,
    sector TEXT,
    headline TEXT NOT NULL,
    source TEXT NOT NULL,
    sentiment REAL,
    magnitude INTEGER,
    catalyst_type TEXT,
    raw_text TEXT,
    llm_analysis TEXT
);
CREATE INDEX IF NOT EXISTS idx_catalysts_sym ON catalysts(symbol, timestamp);

-- Trade log
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_time INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    entry_shares INTEGER NOT NULL,
    exit_time INTEGER,
    exit_price REAL,
    exit_shares INTEGER,
    pnl REAL,
    pnl_pct REAL,
    signal_score REAL,
    signal_reason TEXT,
    exit_reason TEXT,
    catalyst_id INTEGER REFERENCES catalysts(id),
    regime TEXT,
    sector TEXT
);

-- Account snapshots (for equity curve)
CREATE TABLE IF NOT EXISTS account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    net_liquidation REAL NOT NULL,
    cash REAL,
    buying_power REAL,
    day_trades_remaining INTEGER,
    daily_pnl REAL,
    open_positions INTEGER,
    portfolio_heat REAL
);

-- Market regime log
CREATE TABLE IF NOT EXISTS regime_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    regime TEXT NOT NULL,
    spy_vs_20sma REAL,
    spy_vs_50sma REAL,
    vix REAL,
    regime_factor REAL
);
"""


async def get_db() -> aiosqlite.Connection:
    """Open a connection to the SQLite database."""
    db = await aiosqlite.connect(DB_PATH)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row
    return db


async def insert_catalyst(
    db: aiosqlite.Connection,
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
    """Insert a catalyst row and return its rowid."""
    cur = await db.execute(
        """INSERT INTO catalysts
           (timestamp, symbol, sector, headline, source, sentiment, magnitude,
            catalyst_type, raw_text, llm_analysis)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            timestamp, symbol, sector, headline, source, sentiment, magnitude,
            catalyst_type, raw_text, llm_analysis,
        ),
    )
    await db.commit()
    return cur.lastrowid  # type: ignore[return-value]


async def query_catalysts(
    db: aiosqlite.Connection,
    *,
    since_ts: int,
    symbol: str | None = None,
) -> list[dict]:
    """Return catalyst rows newer than *since_ts*, optionally for a symbol."""
    if symbol:
        cur = await db.execute(
            "SELECT * FROM catalysts WHERE timestamp >= ? AND symbol = ? ORDER BY timestamp DESC",
            (since_ts, symbol),
        )
    else:
        cur = await db.execute(
            "SELECT * FROM catalysts WHERE timestamp >= ? ORDER BY timestamp DESC",
            (since_ts,),
        )
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in rows]


async def headline_exists(db: aiosqlite.Connection, headline_hash: str) -> bool:
    """Check if a catalyst with this headline already exists (by exact headline match)."""
    cur = await db.execute(
        "SELECT 1 FROM catalysts WHERE headline = ? LIMIT 1",
        (headline_hash,),
    )
    return (await cur.fetchone()) is not None


async def insert_bars(db: aiosqlite.Connection, bars: list[dict]) -> int:
    """Bulk INSERT OR REPLACE bars. Each bar is a dict with Bar model fields.

    Returns the number of rows inserted.
    """
    if not bars:
        return 0
    await db.executemany(
        """INSERT OR REPLACE INTO bars
           (symbol, timestamp, timeframe, open, high, low, close, volume, vwap, trade_count)
           VALUES (:symbol, :timestamp, :timeframe, :open, :high, :low, :close, :volume, :vwap, :trade_count)""",
        bars,
    )
    await db.commit()
    return len(bars)


async def query_bars(
    db: aiosqlite.Connection,
    symbol: str,
    timeframe: str,
    since_ts: int | None = None,
    until_ts: int | None = None,
) -> list[dict]:
    """Return bar rows for a symbol/timeframe, optionally filtered by timestamp range."""
    clauses = ["symbol = ?", "timeframe = ?"]
    params: list = [symbol, timeframe]
    if since_ts is not None:
        clauses.append("timestamp >= ?")
        params.append(since_ts)
    if until_ts is not None:
        clauses.append("timestamp <= ?")
        params.append(until_ts)
    where = " AND ".join(clauses)
    cur = await db.execute(
        f"SELECT * FROM bars WHERE {where} ORDER BY timestamp ASC",
        params,
    )
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in rows]


async def get_latest_bar_timestamp(
    db: aiosqlite.Connection, symbol: str, timeframe: str
) -> int | None:
    """Return the most recent bar timestamp for a symbol/timeframe, or None."""
    cur = await db.execute(
        "SELECT MAX(timestamp) FROM bars WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    )
    row = await cur.fetchone()
    return row[0] if row and row[0] is not None else None


async def cleanup_old_bars(
    db: aiosqlite.Connection,
    now_ts: int,
    retention_1m_days: int = 5,
    retention_5m_days: int = 90,
) -> None:
    """Delete expired intraday bars based on retention policy."""
    secs_per_day = 86400
    cutoff_1m = now_ts - retention_1m_days * secs_per_day
    cutoff_5m = now_ts - retention_5m_days * secs_per_day
    await db.execute(
        "DELETE FROM bars WHERE timeframe = '1m' AND timestamp < ?", (cutoff_1m,)
    )
    await db.execute(
        "DELETE FROM bars WHERE timeframe = '5m' AND timestamp < ?", (cutoff_5m,)
    )
    await db.commit()


async def insert_trade(
    db: aiosqlite.Connection,
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
    """Insert a completed trade and return its rowid."""
    cur = await db.execute(
        """INSERT INTO trades
           (symbol, direction, entry_time, entry_price, entry_shares,
            exit_time, exit_price, exit_shares, pnl, pnl_pct,
            signal_score, signal_reason, exit_reason, catalyst_id, regime, sector)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            symbol, direction, entry_time, entry_price, entry_shares,
            exit_time, exit_price, exit_shares, pnl, pnl_pct,
            signal_score, signal_reason, exit_reason, catalyst_id, regime, sector,
        ),
    )
    await db.commit()
    return cur.lastrowid  # type: ignore[return-value]


async def query_trades(
    db: aiosqlite.Connection,
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
    if since_ts is not None:
        clauses.append("exit_time >= ?")
        params.append(since_ts)
    if symbol is not None:
        clauses.append("symbol = ?")
        params.append(symbol)
    if sector is not None:
        clauses.append("sector = ?")
        params.append(sector)
    if regime is not None:
        clauses.append("regime = ?")
        params.append(regime)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cur = await db.execute(
        f"SELECT * FROM trades{where} ORDER BY exit_time DESC LIMIT ?",
        params + [limit],
    )
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in rows]


async def get_trade_summary(
    db: aiosqlite.Connection,
    *,
    since_ts: int,
) -> dict:
    """Return aggregate stats: count, total_pnl, win_count, loss_count."""
    cur = await db.execute(
        """SELECT
               COUNT(*) AS count,
               COALESCE(SUM(pnl), 0.0) AS total_pnl,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS win_count,
               SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) AS loss_count
           FROM trades
           WHERE exit_time >= ?""",
        (since_ts,),
    )
    row = await cur.fetchone()
    return {
        "count": row[0],
        "total_pnl": row[1],
        "win_count": row[2] or 0,
        "loss_count": row[3] or 0,
    }


async def init_db() -> None:
    """Create all tables and indexes if they don't exist."""
    db = await get_db()
    try:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    finally:
        await db.close()
