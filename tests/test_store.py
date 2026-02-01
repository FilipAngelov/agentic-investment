"""Tests for SQLite persistence layer."""

import pytest
import aiosqlite

from data.store import SCHEMA_SQL


EXPECTED_TABLES = {
    "bars",
    "sector_snapshots",
    "catalysts",
    "trades",
    "account_snapshots",
    "regime_log",
}


@pytest.fixture
async def db(tmp_path):
    """Create an in-memory-like temp DB with schema applied."""
    db_path = tmp_path / "test.db"
    db = await aiosqlite.connect(db_path)
    await db.executescript(SCHEMA_SQL)
    await db.commit()
    yield db
    await db.close()


async def test_all_tables_created(db):
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    tables = {row[0] for row in await cursor.fetchall()}
    assert tables == EXPECTED_TABLES


async def test_bars_unique_constraint(db):
    await db.execute(
        "INSERT INTO bars (symbol, timestamp, timeframe, open, high, low, close, volume) "
        "VALUES ('AAPL', 1000, '1d', 150.0, 155.0, 149.0, 153.0, 1000000)"
    )
    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            "INSERT INTO bars (symbol, timestamp, timeframe, open, high, low, close, volume) "
            "VALUES ('AAPL', 1000, '1d', 151.0, 156.0, 150.0, 154.0, 2000000)"
        )


async def test_trades_insert_and_read(db):
    await db.execute(
        "INSERT INTO trades (symbol, direction, entry_time, entry_price, entry_shares, regime) "
        "VALUES ('NVDA', 'LONG', 1000, 500.0, 10, 'bull')"
    )
    await db.commit()
    cursor = await db.execute("SELECT symbol, direction, entry_shares FROM trades")
    row = await cursor.fetchone()
    assert row == ("NVDA", "LONG", 10)


async def test_regime_log_insert(db):
    await db.execute(
        "INSERT INTO regime_log (timestamp, regime, spy_vs_20sma, spy_vs_50sma, vix, regime_factor) "
        "VALUES (1000, 'strong_bull', 1.02, 1.05, 13.5, 1.5)"
    )
    await db.commit()
    cursor = await db.execute("SELECT regime, regime_factor FROM regime_log")
    row = await cursor.fetchone()
    assert row == ("strong_bull", 1.5)


async def test_catalyst_foreign_key_in_trades(db):
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute(
        "INSERT INTO catalysts (timestamp, headline, source, magnitude) "
        "VALUES (1000, 'FDA approval', 'SEC', 5)"
    )
    await db.execute(
        "INSERT INTO trades (symbol, direction, entry_time, entry_price, entry_shares, catalyst_id) "
        "VALUES ('MRNA', 'LONG', 1000, 100.0, 20, 1)"
    )
    await db.commit()
    cursor = await db.execute("SELECT catalyst_id FROM trades WHERE symbol='MRNA'")
    row = await cursor.fetchone()
    assert row[0] == 1
