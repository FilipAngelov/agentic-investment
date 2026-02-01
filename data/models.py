"""Pydantic v2 data models matching SQLite schema and runtime needs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from config.sectors import (
    REGIME_BEAR,
    REGIME_BULL,
    REGIME_CHOPPY,
    REGIME_CRISIS,
    REGIME_STRONG_BULL,
)

RegimeType = Literal["strong_bull", "bull", "choppy", "bear", "crisis"]
DirectionType = Literal["LONG", "SHORT"]
TimeframeType = Literal["1m", "5m", "15m", "1d"]


# ---------------------------------------------------------------------------
# DB-backed models
# ---------------------------------------------------------------------------


class Bar(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    symbol: str
    timestamp: int
    timeframe: TimeframeType
    low: float
    high: float
    open: float
    close: float
    volume: int
    vwap: float | None = None
    trade_count: int | None = None

    @field_validator("volume")
    @classmethod
    def volume_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("volume must be >= 0")
        return v

    @field_validator("high")
    @classmethod
    def high_gte_low(cls, v: float, info) -> float:
        low = info.data.get("low")
        if low is not None and v < low:
            raise ValueError("high must be >= low")
        return v

    @field_validator("open")
    @classmethod
    def open_in_range(cls, v: float, info) -> float:
        low = info.data.get("low")
        high = info.data.get("high")
        if low is not None and v < low:
            raise ValueError("open must be >= low")
        if high is not None and v > high:
            raise ValueError("open must be <= high")
        return v

    @field_validator("close")
    @classmethod
    def close_in_range(cls, v: float, info) -> float:
        low = info.data.get("low")
        high = info.data.get("high")
        if low is not None and v < low:
            raise ValueError("close must be >= low")
        if high is not None and v > high:
            raise ValueError("close must be <= high")
        return v


class SectorSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    timestamp: int
    sector: str
    price: float
    change_pct: float | None = None
    volume: int | None = None
    relative_strength: float | None = None
    momentum_score: float | None = None


class Catalyst(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    timestamp: int
    symbol: str | None = None
    sector: str | None = None
    headline: str
    source: str
    sentiment: float | None = None
    magnitude: int | None = None
    catalyst_type: str | None = None
    raw_text: str | None = None
    llm_analysis: str | None = None

    @field_validator("sentiment")
    @classmethod
    def sentiment_range(cls, v: float | None) -> float | None:
        if v is not None and not (-1.0 <= v <= 1.0):
            raise ValueError("sentiment must be between -1.0 and 1.0")
        return v

    @field_validator("magnitude")
    @classmethod
    def magnitude_range(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 5):
            raise ValueError("magnitude must be between 1 and 5")
        return v


class Trade(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    symbol: str
    direction: DirectionType
    entry_time: int
    entry_price: float
    entry_shares: int
    exit_time: int | None = None
    exit_price: float | None = None
    exit_shares: int | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    signal_score: float | None = None
    signal_reason: str | None = None
    exit_reason: str | None = None
    catalyst_id: int | None = None
    regime: str | None = None
    sector: str | None = None


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    timestamp: int
    net_liquidation: float
    cash: float | None = None
    buying_power: float | None = None
    day_trades_remaining: int | None = None
    daily_pnl: float | None = None
    open_positions: int | None = None
    portfolio_heat: float | None = None


class RegimeLog(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    timestamp: int
    regime: RegimeType
    spy_vs_20sma: float | None = None
    spy_vs_50sma: float | None = None
    vix: float | None = None
    regime_factor: float | None = None


# ---------------------------------------------------------------------------
# Runtime-only models (not persisted)
# ---------------------------------------------------------------------------


class Signal(BaseModel):
    symbol: str
    direction: DirectionType
    entry_price: float
    stop_price: float
    target_price: float
    confidence: float
    score: float
    regime: RegimeType
    sector: str | None = None
    reason: str | None = None
    timestamp: int
    volume_conviction: str | None = None
    position_size_factor: float = 1.0
    timeframe_alignment: float | None = None

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v


class Position(BaseModel):
    symbol: str
    direction: DirectionType
    shares: int
    entry_price: float
    entry_time: int
    current_price: float
    stop_price: float
    unrealized_pnl: float
    sector: str | None = None
