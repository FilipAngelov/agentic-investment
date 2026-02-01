"""Tests for TradeLogger and trade store functions."""

from __future__ import annotations

import pytest

from data.store import insert_trade, query_trades, get_trade_summary
from data.models import ClosedTrade, Signal
from portfolio.trade_logger import TradeLogger, EntryContext


def _make_signal(**overrides) -> Signal:
    defaults = dict(
        symbol="AAPL",
        direction="LONG",
        entry_price=150.0,
        stop_price=145.0,
        target_price=160.0,
        confidence=0.8,
        score=0.75,
        regime="bull",
        sector="tech",
        reason="breakout",
        timestamp=1000,
    )
    defaults.update(overrides)
    return Signal(**defaults)


def _make_closed(**overrides) -> ClosedTrade:
    defaults = dict(
        symbol="AAPL",
        direction="LONG",
        shares=10,
        entry_price=150.0,
        exit_price=160.0,
        entry_time=1000,
        exit_time=2000,
        pnl=100.0,
        pnl_pct=6.67,
    )
    defaults.update(overrides)
    return ClosedTrade(**defaults)


# --- TradeLogger unit tests ---


class TestRecordEntry:
    def test_stashes_context(self):
        logger = TradeLogger()
        sig = _make_signal()
        logger.record_entry(sig, shares=10, catalyst_id=42)
        ctx = logger._entry_context["AAPL"]
        assert ctx.signal_score == 0.75
        assert ctx.regime == "bull"
        assert ctx.sector == "tech"
        assert ctx.catalyst_id == 42

    def test_reason_serialized(self):
        logger = TradeLogger()
        sig = _make_signal(reason="breakout")
        logger.record_entry(sig, shares=10)
        ctx = logger._entry_context["AAPL"]
        assert ctx.signal_reason == '"breakout"'

    def test_json_reason_preserved(self):
        logger = TradeLogger()
        sig = _make_signal(reason='{"key": "val"}')
        logger.record_entry(sig, shares=10)
        ctx = logger._entry_context["AAPL"]
        assert ctx.signal_reason == '{"key": "val"}'


class TestRecordExit:
    @pytest.mark.asyncio
    async def test_persists_trade_with_context(self, db):
        logger = TradeLogger()
        cat_id = await db.fetchval(
            "INSERT INTO catalysts (timestamp, headline, source) VALUES ($1, $2, $3) RETURNING id",
            1000, "test", "test",
        )
        logger.record_entry(_make_signal(), shares=10, catalyst_id=cat_id)
        closed = _make_closed()
        row_id = await logger.record_exit(db, closed, "trailing_stop")
        assert row_id >= 1
        rows = await query_trades(db)
        assert len(rows) == 1
        r = rows[0]
        assert r["symbol"] == "AAPL"
        assert r["signal_score"] == 0.75
        assert r["exit_reason"] == "trailing_stop"
        assert r["regime"] == "bull"
        assert r["sector"] == "tech"
        assert r["catalyst_id"] == cat_id

    @pytest.mark.asyncio
    async def test_without_entry_context(self, db):
        logger = TradeLogger()
        closed = _make_closed(symbol="MSFT")
        row_id = await logger.record_exit(db, closed, "manual_close")
        assert row_id >= 1
        rows = await query_trades(db, symbol="MSFT")
        assert len(rows) == 1
        assert rows[0]["signal_score"] is None
        assert rows[0]["exit_reason"] == "manual_close"

    @pytest.mark.asyncio
    async def test_partial_exit_preserves_context(self, db):
        logger = TradeLogger()
        logger.record_entry(_make_signal(), shares=20)
        await logger.record_exit(db, _make_closed(shares=10), "partial_exit_tier_1")
        assert "AAPL" in logger._entry_context
        await logger.record_exit(db, _make_closed(shares=10), "partial_exit_tier_2")
        rows = await query_trades(db)
        assert len(rows) == 2
        assert all(r["signal_score"] == 0.75 for r in rows)

    @pytest.mark.asyncio
    async def test_clear_context_on_full_close(self, db):
        logger = TradeLogger()
        logger.record_entry(_make_signal(), shares=10)
        await logger.record_exit(db, _make_closed(), "trailing_stop")
        logger.clear_context("AAPL")
        assert "AAPL" not in logger._entry_context


class TestRecordExits:
    @pytest.mark.asyncio
    async def test_batch(self, db):
        logger = TradeLogger()
        logger.record_entry(_make_signal(), shares=30)
        trades = [
            _make_closed(shares=10, pnl=50.0),
            _make_closed(shares=10, pnl=60.0),
            _make_closed(shares=10, pnl=70.0),
        ]
        ids = await logger.record_exits(db, trades, "time_decay")
        assert len(ids) == 3
        rows = await query_trades(db)
        assert len(rows) == 3


# --- Store function tests ---


class TestInsertTrade:
    @pytest.mark.asyncio
    async def test_insert_and_retrieve(self, db):
        row_id = await insert_trade(
            db,
            symbol="TSLA", direction="LONG",
            entry_time=1000, entry_price=200.0, entry_shares=5,
            exit_time=2000, exit_price=210.0, exit_shares=5,
            pnl=50.0, pnl_pct=5.0,
            signal_score=0.9, signal_reason='"momentum"',
            exit_reason="target_hit", catalyst_id=None,
            regime="bull", sector="auto",
        )
        assert row_id >= 1
        rows = await query_trades(db, symbol="TSLA")
        assert len(rows) == 1
        assert rows[0]["pnl"] == 50.0


class TestQueryTrades:
    @pytest.fixture
    async def seeded_db(self, db):
        for i, (sym, sec, reg, ts) in enumerate([
            ("AAPL", "tech", "bull", 1000),
            ("MSFT", "tech", "bear", 2000),
            ("XOM", "energy", "bull", 3000),
        ]):
            await insert_trade(
                db, symbol=sym, direction="LONG",
                entry_time=ts - 500, entry_price=100.0, entry_shares=10,
                exit_time=ts, exit_price=110.0, exit_shares=10,
                pnl=100.0 * (i + 1), pnl_pct=10.0,
                signal_score=0.5, signal_reason=None,
                exit_reason="trailing_stop", catalyst_id=None,
                regime=reg, sector=sec,
            )
        return db

    @pytest.mark.asyncio
    async def test_filter_by_symbol(self, seeded_db):
        rows = await query_trades(seeded_db, symbol="AAPL")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_filter_by_sector(self, seeded_db):
        rows = await query_trades(seeded_db, sector="tech")
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_filter_by_regime(self, seeded_db):
        rows = await query_trades(seeded_db, regime="bull")
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_filter_by_since_ts(self, seeded_db):
        rows = await query_trades(seeded_db, since_ts=2000)
        assert len(rows) == 2


class TestGetTradeSummary:
    @pytest.mark.asyncio
    async def test_aggregates(self, db):
        for pnl in [100.0, -50.0, 200.0, -30.0]:
            await insert_trade(
                db, symbol="TEST", direction="LONG",
                entry_time=500, entry_price=100.0, entry_shares=10,
                exit_time=1000, exit_price=110.0, exit_shares=10,
                pnl=pnl, pnl_pct=1.0,
                signal_score=None, signal_reason=None,
                exit_reason="test", catalyst_id=None,
                regime=None, sector=None,
            )
        summary = await get_trade_summary(db, since_ts=0)
        assert summary["count"] == 4
        assert summary["total_pnl"] == pytest.approx(220.0)
        assert summary["win_count"] == 2
        assert summary["loss_count"] == 2
