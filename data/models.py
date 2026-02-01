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
    expected_move_pct: float | None = None
    atr: float | None = None

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v

    @field_validator("score")
    @classmethod
    def score_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("score must be between 0.0 and 1.0")
        return v

    @field_validator("stop_price")
    @classmethod
    def stop_direction_consistent(cls, v: float, info) -> float:
        direction = info.data.get("direction")
        entry = info.data.get("entry_price")
        if direction is None or entry is None:
            return v
        if direction == "LONG" and v >= entry:
            raise ValueError("LONG stop_price must be below entry_price")
        if direction == "SHORT" and v <= entry:
            raise ValueError("SHORT stop_price must be above entry_price")
        return v

    @field_validator("target_price")
    @classmethod
    def target_direction_consistent(cls, v: float, info) -> float:
        direction = info.data.get("direction")
        entry = info.data.get("entry_price")
        if direction is None or entry is None:
            return v
        if direction == "LONG" and v <= entry:
            raise ValueError("LONG target_price must be above entry_price")
        if direction == "SHORT" and v >= entry:
            raise ValueError("SHORT target_price must be below entry_price")
        return v

    @field_validator("atr")
    @classmethod
    def atr_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("atr must be positive")
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


class ClosedTrade(BaseModel):
    """Record of a closed (or partially closed) position slice."""

    symbol: str
    direction: DirectionType
    shares: int
    entry_price: float
    exit_price: float
    entry_time: int
    exit_time: int
    pnl: float
    pnl_pct: float


# ---------------------------------------------------------------------------
# Pre-trade compliance models
# ---------------------------------------------------------------------------


class AccountState(BaseModel):
    """IBKR account fields fetched once per trade cycle."""

    net_liquidation: float
    total_cash_value: float
    buying_power: float
    available_funds: float
    excess_liquidity: float
    init_margin_req: float
    maint_margin_req: float
    sma: float
    day_trades_remaining: int  # -1 = unlimited (PDT qualified)
    daily_pnl: float = 0.0


class ProposedOrder(BaseModel):
    """What the guard evaluates before submission."""

    symbol: str
    direction: DirectionType
    shares: int
    limit_price: float
    stop_price: float
    sector: str | None = None
    is_closing: bool = False
    opened_today: bool = False  # for day-trade detection

    @field_validator("shares")
    @classmethod
    def shares_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("shares must be > 0")
        return v


class PreTradeResult(BaseModel):
    """Result of pre-trade compliance check."""

    approved: bool
    reason: str = ""
    checks_passed: list[str] = []
    checks_failed: list[str] = []


class OrderResult(BaseModel):
    """Result of an order execution attempt."""

    success: bool
    symbol: str
    direction: DirectionType
    shares: int = 0
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    order_ids: list[int] = []  # [parent, stop, target]
    reason: str = ""  # rejection/failure reason
    pre_trade: PreTradeResult | None = None


class StopUpdate(BaseModel):
    """Result of a trailing stop recalculation."""

    symbol: str
    old_stop: float
    new_stop: float
    moved: bool  # True if stop was tightened
    profit_atr: float  # profit in ATR units
    k_used: float  # which k-multiplier was applied
    regime_factor: float


class PartialExitSignal(BaseModel):
    """Recommendation to partially close a position."""

    symbol: str
    direction: DirectionType
    tier: int  # 1 or 2
    shares_to_sell: int
    profit_atr: float  # current profit in ATR units
    limit_price: float  # current price for the exit order


class TimeDecaySignal(BaseModel):
    """Recommendation from time-based decay evaluation."""

    symbol: str
    direction: DirectionType
    action: Literal["tighten_stop", "exit"]
    reason: str
    hours_held: float
    profit_atr: float
    new_stop: float | None = None  # set when action == "tighten_stop"


class RiskStatus(BaseModel):
    """Snapshot of portfolio-level risk assessment."""

    timestamp: int
    daily_pnl: float
    drawdown_limit: float
    drawdown_breached: bool
    portfolio_heat: float
    max_portfolio_heat: float
    heat_breached: bool
    sector_heats: dict[str, float] = {}
    max_sector_heat: float
    sector_breached: list[str] = []
    concentration_clusters: list[str] = []
    halt_trading: bool
    warnings: list[str] = []


class ReconciliationResult(BaseModel):
    """Result of bot + protected vs IBKR reconciliation."""

    matches: bool
    mismatches: list[str] = []


class ReconciliationSnapshot(BaseModel):
    """Timestamped reconciliation result with detail."""

    timestamp: int
    matches: bool
    mismatches: list[str] = []
    bot_total_symbols: int
    ibkr_total_symbols: int
    protected_total_symbols: int
