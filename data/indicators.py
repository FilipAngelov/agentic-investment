"""Derived indicators: SMA, EMA, RSI, MACD, ATR, relative strength."""

from __future__ import annotations

import math


def sma(values: list[float], period: int) -> list[float | None]:
    """Simple moving average. Returns list same length as input."""
    if period <= 0 or not values:
        return [None] * len(values)
    result: list[float | None] = [None] * len(values)
    if period > len(values):
        return result
    window_sum = sum(values[:period])
    result[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        result[i] = window_sum / period
    return result


def ema(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average using multiplier 2/(period+1)."""
    if period <= 0 or not values:
        return [None] * len(values)
    result: list[float | None] = [None] * len(values)
    if period > len(values):
        return result
    # Seed with SMA
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        val = values[i] * k + prev * (1 - k)
        result[i] = val
        prev = val
    return result


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """RSI with Wilder smoothing (exponential)."""
    if period <= 0 or len(closes) < period + 1:
        return [None] * len(closes)
    result: list[float | None] = [None] * len(closes)
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    # First average: simple average of first `period` changes
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - 100.0 / (1.0 + rs)
    # Wilder smoothing for the rest
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - 100.0 / (1.0 + rs)
    return result


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD: returns (macd_line, signal_line, histogram)."""
    n = len(closes)
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd_line: list[float | None] = [None] * n
    for i in range(n):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            macd_line[i] = fast_ema[i] - slow_ema[i]
    # Signal line = EMA of MACD values (skip Nones)
    macd_vals: list[float] = [v for v in macd_line if v is not None]
    sig_ema = ema(macd_vals, signal_period) if macd_vals else []
    signal_line: list[float | None] = [None] * n
    histogram: list[float | None] = [None] * n
    # Map signal EMA back to original indices
    j = 0
    for i in range(n):
        if macd_line[i] is not None:
            if j < len(sig_ema):
                signal_line[i] = sig_ema[j]
            if signal_line[i] is not None and macd_line[i] is not None:
                histogram[i] = macd_line[i] - signal_line[i]
            j += 1
    return macd_line, signal_line, histogram


def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[float | None]:
    """Average True Range using Wilder smoothing."""
    n = len(closes)
    if n < 2 or period <= 0 or period >= n:
        return [None] * n
    result: list[float | None] = [None] * n
    # True ranges (starting from index 1)
    trs: list[float] = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return result
    # First ATR is simple average
    atr_val = sum(trs[:period]) / period
    result[period] = atr_val
    # Wilder smoothing
    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
        result[i + 1] = atr_val
    return result


def rate_of_change(values: list[float], period: int) -> list[float | None]:
    """Rate of change: (current - past) / past * 100."""
    if period <= 0 or not values:
        return [None] * len(values)
    result: list[float | None] = [None] * len(values)
    for i in range(period, len(values)):
        if values[i - period] != 0:
            result[i] = (values[i] - values[i - period]) / values[i - period] * 100.0
        else:
            result[i] = None
    return result


def historical_volatility(closes: list[float], period: int = 20) -> list[float | None]:
    """Annualized historical volatility from log returns."""
    n = len(closes)
    if n < period + 1 or period <= 1:
        return [None] * n
    result: list[float | None] = [None] * n
    log_returns: list[float] = []
    for i in range(1, n):
        if closes[i - 1] > 0 and closes[i] > 0:
            log_returns.append(math.log(closes[i] / closes[i - 1]))
        else:
            log_returns.append(0.0)
    for i in range(period - 1, len(log_returns)):
        window = log_returns[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((r - mean) ** 2 for r in window) / (period - 1)
        result[i + 1] = math.sqrt(variance) * math.sqrt(252)
    return result


def relative_volume(volumes: list[int], period: int = 20) -> list[float | None]:
    """Current volume / average volume over period."""
    if period <= 0 or not volumes:
        return [None] * len(volumes)
    result: list[float | None] = [None] * len(volumes)
    vol_floats = [float(v) for v in volumes]
    avg = sma(vol_floats, period)
    for i in range(len(volumes)):
        if avg[i] is not None and avg[i] > 0:
            result[i] = vol_floats[i] / avg[i]
    return result
