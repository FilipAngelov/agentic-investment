"""Tests for PostgreSQL persistence layer."""

import pytest
import asyncpg


EXPECTED_TABLES = {
    "bars",
    "sector_snapshots",
    "catalysts",
    "trades",
    "account_snapshots",
    "regime_log",
}


async def test_all_tables_created(db):
    rows = await db.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    )
    tables = {row["tablename"] for row in rows}
    assert EXPECTED_TABLES.issubset(tables)


async def test_bars_unique_constraint(db):
    await db.execute(
        "INSERT INTO bars (symbol, timestamp, timeframe, open, high, low, close, volume) "
        "VALUES ('AAPL', 1000, '1d', 150.0, 155.0, 149.0, 153.0, 1000000)"
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO bars (symbol, timestamp, timeframe, open, high, low, close, volume) "
            "VALUES ('AAPL', 1000, '1d', 151.0, 156.0, 150.0, 154.0, 2000000)"
        )


async def test_trades_insert_and_read(db):
    await db.execute(
        "INSERT INTO trades (symbol, direction, entry_time, entry_price, entry_shares, regime) "
        "VALUES ('NVDA', 'LONG', 1000, 500.0, 10, 'bull')"
    )
    row = await db.fetchrow("SELECT symbol, direction, entry_shares FROM trades")
    assert row["symbol"] == "NVDA"
    assert row["direction"] == "LONG"
    assert row["entry_shares"] == 10


async def test_regime_log_insert(db):
    await db.execute(
        "INSERT INTO regime_log (timestamp, regime, spy_vs_20sma, spy_vs_50sma, vix, regime_factor) "
        "VALUES (1000, 'strong_bull', 1.02, 1.05, 13.5, 1.5)"
    )
    row = await db.fetchrow("SELECT regime, regime_factor FROM regime_log")
    assert row["regime"] == "strong_bull"
    assert row["regime_factor"] == 1.5


async def test_catalyst_foreign_key_in_trades(db):
    await db.execute(
        "INSERT INTO catalysts (timestamp, headline, source, magnitude) "
        "VALUES (1000, 'FDA approval', 'SEC', 5)"
    )
    cat_id = await db.fetchval("SELECT id FROM catalysts WHERE headline = 'FDA approval'")
    await db.execute(
        "INSERT INTO trades (symbol, direction, entry_time, entry_price, entry_shares, catalyst_id) "
        "VALUES ('MRNA', 'LONG', 1000, 100.0, 20, $1)", cat_id
    )
    row = await db.fetchrow("SELECT catalyst_id FROM trades WHERE symbol='MRNA'")
    assert row["catalyst_id"] == cat_id
