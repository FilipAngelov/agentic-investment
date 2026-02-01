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


async def init_db() -> None:
    """Create all tables and indexes if they don't exist."""
    db = await get_db()
    try:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    finally:
        await db.close()
