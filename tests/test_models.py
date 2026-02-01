"""Tests for Pydantic data models."""

import pytest
from pydantic import ValidationError

from data.models import (
    AccountSnapshot,
    Bar,
    Catalyst,
    Position,
    RegimeLog,
    SectorSnapshot,
    Signal,
    Trade,
)


# ── Bar ───────────────────────────────────────────────────────────────────


class TestBar:
    def test_valid(self):
        b = Bar(symbol="AAPL", timestamp=1000, timeframe="5m",
                open=150.0, high=155.0, low=148.0, close=153.0, volume=1000)
        assert b.symbol == "AAPL"
        assert b.id is None
        assert b.vwap is None

    def test_ohlc_low_gt_high_rejected(self):
        with pytest.raises(ValidationError):
            Bar(symbol="AAPL", timestamp=1000, timeframe="1d",
                open=150.0, high=140.0, low=148.0, close=139.0, volume=100)

    def test_open_below_low_rejected(self):
        with pytest.raises(ValidationError):
            Bar(symbol="AAPL", timestamp=1000, timeframe="1d",
                open=90.0, high=155.0, low=100.0, close=150.0, volume=100)

    def test_close_above_high_rejected(self):
        with pytest.raises(ValidationError):
            Bar(symbol="AAPL", timestamp=1000, timeframe="1d",
                open=150.0, high=155.0, low=148.0, close=160.0, volume=100)

    def test_negative_volume_rejected(self):
        with pytest.raises(ValidationError):
            Bar(symbol="AAPL", timestamp=1000, timeframe="1d",
                open=150.0, high=155.0, low=148.0, close=153.0, volume=-1)

    def test_invalid_timeframe_rejected(self):
        with pytest.raises(ValidationError):
            Bar(symbol="AAPL", timestamp=1000, timeframe="2m",
                open=150.0, high=155.0, low=148.0, close=153.0, volume=100)

    def test_model_dump_roundtrip(self):
        b = Bar(symbol="AAPL", timestamp=1000, timeframe="1m",
                open=100.0, high=105.0, low=99.0, close=103.0, volume=500)
        b2 = Bar(**b.model_dump())
        assert b == b2


# ── SectorSnapshot ────────────────────────────────────────────────────────


class TestSectorSnapshot:
    def test_valid(self):
        s = SectorSnapshot(timestamp=1000, sector="Technology", price=180.0)
        assert s.id is None
        assert s.change_pct is None


# ── Catalyst ──────────────────────────────────────────────────────────────


class TestCatalyst:
    def test_valid(self):
        c = Catalyst(timestamp=1000, headline="Fed raises rates",
                     source="reuters", sentiment=0.5, magnitude=3)
        assert c.sentiment == 0.5

    def test_sentiment_too_high(self):
        with pytest.raises(ValidationError):
            Catalyst(timestamp=1000, headline="x", source="y", sentiment=1.5)

    def test_sentiment_too_low(self):
        with pytest.raises(ValidationError):
            Catalyst(timestamp=1000, headline="x", source="y", sentiment=-1.1)

    def test_magnitude_too_high(self):
        with pytest.raises(ValidationError):
            Catalyst(timestamp=1000, headline="x", source="y", magnitude=6)

    def test_magnitude_too_low(self):
        with pytest.raises(ValidationError):
            Catalyst(timestamp=1000, headline="x", source="y", magnitude=0)

    def test_defaults_none(self):
        c = Catalyst(timestamp=1000, headline="x", source="y")
        assert c.symbol is None
        assert c.magnitude is None


# ── Trade ─────────────────────────────────────────────────────────────────


class TestTrade:
    def test_valid(self):
        t = Trade(symbol="TSLA", direction="LONG", entry_time=1000,
                  entry_price=200.0, entry_shares=10)
        assert t.exit_time is None

    def test_invalid_direction(self):
        with pytest.raises(ValidationError):
            Trade(symbol="TSLA", direction="UP", entry_time=1000,
                  entry_price=200.0, entry_shares=10)


# ── AccountSnapshot ───────────────────────────────────────────────────────


class TestAccountSnapshot:
    def test_valid(self):
        a = AccountSnapshot(timestamp=1000, net_liquidation=50000.0)
        assert a.cash is None


# ── RegimeLog ─────────────────────────────────────────────────────────────


class TestRegimeLog:
    def test_valid(self):
        r = RegimeLog(timestamp=1000, regime="strong_bull")
        assert r.regime_factor is None

    def test_invalid_regime(self):
        with pytest.raises(ValidationError):
            RegimeLog(timestamp=1000, regime="sideways")


# ── Signal ────────────────────────────────────────────────────────────────


class TestSignal:
    def test_valid(self):
        s = Signal(symbol="AAPL", direction="LONG", entry_price=150.0,
                   stop_price=145.0, target_price=160.0, confidence=0.8,
                   score=7.5, regime="bull", timestamp=1000)
        assert s.sector is None

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            Signal(symbol="AAPL", direction="LONG", entry_price=150.0,
                   stop_price=145.0, target_price=160.0, confidence=1.5,
                   score=7.5, regime="bull", timestamp=1000)

    def test_confidence_negative(self):
        with pytest.raises(ValidationError):
            Signal(symbol="AAPL", direction="LONG", entry_price=150.0,
                   stop_price=145.0, target_price=160.0, confidence=-0.1,
                   score=7.5, regime="bull", timestamp=1000)


# ── Position ──────────────────────────────────────────────────────────────


class TestPosition:
    def test_valid(self):
        p = Position(symbol="MSFT", direction="SHORT", shares=50,
                     entry_price=300.0, entry_time=1000, current_price=295.0,
                     stop_price=310.0, unrealized_pnl=250.0)
        assert p.sector is None
