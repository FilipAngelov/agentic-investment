"""Technical analysis: breakout detection, momentum, volume, signal generation."""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

from config.sectors import REGIME_FACTORS, REGIME_BEAR, REGIME_BEAR_LONG_FACTOR
from config.settings import risk_config
from data.indicators import (
    atr,
    ema,
    macd,
    rate_of_change,
    relative_volume,
    rsi,
    sma,
)
from data.models import Catalyst, DirectionType, RegimeType, Signal

# Minimum confidence to emit a signal
MIN_CONFIDENCE = 0.4


def extract_series(bars: list[dict]) -> dict:
    """Extract price/volume lists from bar dicts."""
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars]
    timestamps = [b["timestamp"] for b in bars]
    return {
        "closes": closes,
        "highs": highs,
        "lows": lows,
        "volumes": volumes,
        "timestamps": timestamps,
    }


def compute_technicals(bars: list[dict]) -> dict:
    """Run all indicators on bar series."""
    s = extract_series(bars)
    c, h, l, v = s["closes"], s["highs"], s["lows"], s["volumes"]
    macd_line, macd_signal, macd_hist = macd(c)
    return {
        "sma_20": sma(c, 20),
        "sma_50": sma(c, 50),
        "ema_12": ema(c, 12),
        "ema_26": ema(c, 26),
        "rsi_14": rsi(c),
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "atr_14": atr(h, l, c),
        "roc_5": rate_of_change(c, 5),
        "roc_20": rate_of_change(c, 20),
        "rvol_20": relative_volume(v, 20),
    }


def detect_breakout(bars: list[dict], technicals: dict) -> dict | None:
    """Detect price breakout above 20-bar high or below 20-bar low."""
    if len(bars) < 21:
        return None
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    last_close = closes[-1]
    sma50 = technicals["sma_50"][-1]
    if sma50 is None:
        return None

    prior_highs = highs[-21:-1]
    prior_lows = lows[-21:-1]
    highest = max(prior_highs)
    lowest = min(prior_lows)

    if last_close > highest and last_close > sma50:
        return {"direction": "LONG", "level": highest, "type": "high_breakout"}
    if last_close < lowest and last_close < sma50:
        return {"direction": "SHORT", "level": lowest, "type": "low_breakdown"}
    return None


def detect_momentum_continuation(
    bars: list[dict], technicals: dict, momentum: dict, volume: dict
) -> dict | None:
    """Detect momentum continuation: SMA20>SMA50 + RSI 50-70 + rvol>=1.5 + MACD hist rising."""
    sma20 = _last_valid(technicals["sma_20"])
    sma50 = _last_valid(technicals["sma_50"])
    if sma20 is None or sma50 is None:
        return None

    rsi_val = momentum["rsi"]
    rvol = volume.get("rvol")
    macd_rising = momentum["macd_histogram_rising"]

    # Long: SMA20 > SMA50, RSI 50-70, rvol >= 1.5, MACD hist rising
    if sma20 > sma50 and rsi_val is not None and 50 <= rsi_val <= 70:
        if rvol is not None and rvol >= 1.5 and macd_rising:
            return {"direction": "LONG", "type": "momentum_continuation"}

    # Short: SMA20 < SMA50, RSI 30-50, rvol >= 1.5, MACD hist falling
    if sma20 < sma50 and rsi_val is not None and 30 <= rsi_val <= 50:
        if rvol is not None and rvol >= 1.5 and not macd_rising:
            return {"direction": "SHORT", "type": "momentum_continuation"}

    return None


def detect_catalyst_driven(
    bars: list[dict],
    technicals: dict,
    momentum: dict,
    volume: dict,
    catalysts: list[Catalyst] | None,
) -> dict | None:
    """Detect catalyst-driven move: max magnitude>=3 + ROC aligned + rvol>=1.5."""
    if not catalysts:
        return None

    max_mag = max((c.magnitude or 0) for c in catalysts)
    if max_mag < 3:
        return None

    rvol = volume.get("rvol")
    if rvol is None or rvol < 1.5:
        return None

    roc5 = momentum.get("roc_5")
    if roc5 is None:
        return None

    # Find dominant sentiment
    best = max(catalysts, key=lambda c: c.magnitude or 0)
    sentiment = best.sentiment

    if sentiment is not None and sentiment > 0 and roc5 > 0:
        return {"direction": "LONG", "type": "catalyst_driven"}
    if sentiment is not None and sentiment < 0 and roc5 < 0:
        return {"direction": "SHORT", "type": "catalyst_driven"}

    return None


def detect_near_breakout(
    bars: list[dict], technicals: dict, momentum: dict, volume: dict
) -> dict | None:
    """Detect near-breakout: within 2% of 20-day high/low + SMA50 trend aligned + rvol>=1.5."""
    if len(bars) < 21:
        return None

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    last_close = closes[-1]

    sma50 = _last_valid(technicals["sma_50"])
    if sma50 is None:
        return None

    rvol = volume.get("rvol")
    if rvol is None or rvol < 1.5:
        return None

    prior_highs = highs[-21:-1]
    prior_lows = lows[-21:-1]
    highest = max(prior_highs)
    lowest = min(prior_lows)

    # Within 2% of 20-day high, above SMA50
    if last_close >= highest * 0.98 and last_close <= highest and last_close > sma50:
        if momentum["trend"] == "up":
            return {"direction": "LONG", "level": highest, "type": "near_breakout"}

    # Within 2% of 20-day low, below SMA50
    if last_close <= lowest * 1.02 and last_close >= lowest and last_close < sma50:
        if momentum["trend"] == "down":
            return {"direction": "SHORT", "level": lowest, "type": "near_breakout"}

    return None


def check_momentum(technicals: dict) -> dict:
    """Evaluate momentum from latest indicator values."""
    rsi_val = _last_valid(technicals["rsi_14"])
    if rsi_val is not None:
        if rsi_val >= 70:
            rsi_signal = "overbought"
        elif rsi_val <= 30:
            rsi_signal = "oversold"
        else:
            rsi_signal = "neutral"
    else:
        rsi_signal = "neutral"

    # MACD cross: check last two histogram values
    macd_cross = None
    hist = technicals["macd_hist"]
    h1 = _last_valid(hist, offset=1)
    h0 = _last_valid(hist)
    if h1 is not None and h0 is not None:
        if h1 <= 0 < h0:
            macd_cross = "bullish"
        elif h1 >= 0 > h0:
            macd_cross = "bearish"

    macd_hist_rising = False
    if h1 is not None and h0 is not None:
        macd_hist_rising = h0 > h1

    # Trend: SMA20 vs SMA50
    sma20 = _last_valid(technicals["sma_20"])
    sma50 = _last_valid(technicals["sma_50"])
    if sma20 is not None and sma50 is not None:
        if sma20 > sma50 * 1.001:
            trend = "up"
        elif sma20 < sma50 * 0.999:
            trend = "down"
        else:
            trend = "flat"
    else:
        trend = "flat"

    roc5 = _last_valid(technicals["roc_5"])
    return {
        "rsi": rsi_val,
        "rsi_signal": rsi_signal,
        "macd_cross": macd_cross,
        "macd_histogram_rising": macd_hist_rising,
        "roc_5": roc5,
        "trend": trend,
    }


def check_volume_confirmation(technicals: dict) -> dict:
    """Evaluate volume conviction."""
    rvol = _last_valid(technicals["rvol_20"])
    if rvol is None:
        return {
            "rvol": None,
            "conviction": "weak",
            "volume_trend": "flat",
            "price_volume_divergence": False,
        }

    if rvol >= 2.0:
        conviction = "strong"
    elif rvol >= 1.5:
        conviction = "moderate"
    else:
        conviction = "weak"

    # Volume trend: compare last rvol to one before
    rvol_prev = _last_valid(technicals["rvol_20"], offset=1)
    if rvol_prev is not None:
        if rvol > rvol_prev * 1.1:
            vol_trend = "rising"
        elif rvol < rvol_prev * 0.9:
            vol_trend = "falling"
        else:
            vol_trend = "flat"
    else:
        vol_trend = "flat"

    # Price-volume divergence: price up but volume falling
    roc = _last_valid(technicals["roc_5"])
    divergence = False
    if roc is not None and roc > 0 and vol_trend == "falling":
        divergence = True

    # Capitulation: price falling + high volume
    capitulation = roc is not None and roc < -1.0 and rvol >= 2.0

    # Volume exhaustion: 3+ consecutive bars of declining volume in an uptrend
    volume_exhaustion = False
    rvol_series = technicals["rvol_20"]
    roc_val = _last_valid(technicals["roc_20"])
    if roc_val is not None and roc_val > 0 and len(rvol_series) >= 4:
        # Check last 3 bars for consecutive decline
        tail = [v for v in rvol_series[-4:] if v is not None]
        if len(tail) >= 4 and all(tail[i] > tail[i + 1] for i in range(len(tail) - 1)):
            volume_exhaustion = True

    return {
        "rvol": rvol,
        "conviction": conviction,
        "volume_trend": vol_trend,
        "price_volume_divergence": divergence,
        "capitulation": capitulation,
        "volume_exhaustion": volume_exhaustion,
    }


def compute_position_size_factor(conviction: str) -> float:
    """Map volume conviction to position size factor."""
    if conviction == "strong":
        return 1.0
    if conviction == "moderate":
        return 0.75
    return 0.5


def compute_stop_target(
    direction: DirectionType,
    entry_price: float,
    atr_val: float,
    regime_factor: float,
) -> tuple[float, float]:
    """Compute initial stop and target prices using ATR."""
    k1 = risk_config.stop_k1
    adjusted_atr = atr_val * regime_factor
    if direction == "LONG":
        stop = entry_price - k1 * adjusted_atr
        target = entry_price + 2.0 * adjusted_atr
    else:
        stop = entry_price + k1 * adjusted_atr
        target = entry_price - 2.0 * adjusted_atr
    return (stop, target)


def generate_technical_signal(
    bars: list[dict],
    regime: RegimeType,
    direction_hint: DirectionType | None = None,
    sector: str | None = None,
    higher_tf_bars: dict[str, list[dict]] | None = None,
    catalysts: list[Catalyst] | None = None,
) -> Signal | None:
    """Orchestrate technical analysis and produce a Signal if conditions met.

    Tries 4 signal paths (breakout, momentum, catalyst, near-breakout),
    picks the highest base confidence, then applies bonuses and filters.
    """
    if len(bars) < 51:
        log.debug("Skipping signal: only %d bars (need 51)", len(bars))
        return None

    technicals = compute_technicals(bars)
    momentum = check_momentum(technicals)
    volume = check_volume_confirmation(technicals)

    # --- Collect candidates from all signal paths ---
    candidates: list[tuple[float, dict, str]] = []  # (base_conf, detection, signal_type)

    breakout = detect_breakout(bars, technicals)
    if breakout is not None:
        candidates.append((0.40, breakout, "breakout"))

    mom_signal = detect_momentum_continuation(bars, technicals, momentum, volume)
    if mom_signal is not None:
        candidates.append((0.30, mom_signal, "momentum"))

    cat_signal = detect_catalyst_driven(bars, technicals, momentum, volume, catalysts)
    if cat_signal is not None:
        candidates.append((0.30, cat_signal, "catalyst"))

    near_bo = detect_near_breakout(bars, technicals, momentum, volume)
    if near_bo is not None:
        candidates.append((0.25, near_bo, "near_breakout"))

    if not candidates:
        log.debug("Skipping signal: no signal path triggered")
        return None

    # Pick highest base confidence
    candidates.sort(key=lambda x: x[0], reverse=True)
    base_confidence, detection, signal_type = candidates[0]

    direction: DirectionType = detection["direction"]
    if direction_hint is not None and direction != direction_hint:
        return None

    # Filter: don't go long if overbought, don't short if oversold
    if direction == "LONG" and momentum["rsi_signal"] == "overbought":
        return None
    if direction == "SHORT" and momentum["rsi_signal"] == "oversold":
        return None

    # Confidence scoring: base + momentum/volume bonuses
    momentum_bonus = 0.0
    if direction == "LONG":
        if momentum["trend"] == "up":
            momentum_bonus += 0.15
        if momentum["macd_cross"] == "bullish" or momentum["macd_histogram_rising"]:
            momentum_bonus += 0.1
    else:
        if momentum["trend"] == "down":
            momentum_bonus += 0.15
        if momentum["macd_cross"] == "bearish" or not momentum["macd_histogram_rising"]:
            momentum_bonus += 0.1

    vol_score = 0.0
    if volume["conviction"] == "strong":
        vol_score = 0.2
    elif volume["conviction"] == "moderate":
        vol_score = 0.1

    if volume["price_volume_divergence"] and direction == "LONG":
        vol_score -= 0.1

    confidence = min(base_confidence + momentum_bonus + vol_score, 1.0)
    if confidence < MIN_CONFIDENCE:
        log.debug(
            "Signal %s filtered: confidence %.3f < %.2f",
            signal_type, confidence, MIN_CONFIDENCE,
        )
        return None

    # Stop/target
    atr_val = _last_valid(technicals["atr_14"])
    if atr_val is None or atr_val <= 0:
        return None

    regime_factor = REGIME_FACTORS.get(regime, 1.0)
    if regime == REGIME_BEAR and direction == "LONG":
        regime_factor = REGIME_BEAR_LONG_FACTOR

    entry_price = bars[-1]["close"]
    stop_price, target_price = compute_stop_target(
        direction, entry_price, atr_val, regime_factor
    )

    reasons: list[str] = []
    reasons.append(f"signal={signal_type}")
    if detection.get("level") is not None:
        reasons.append(f"{detection['type']} at {detection['level']:.2f}")
    else:
        reasons.append(detection["type"])
    reasons.append(f"trend={momentum['trend']}")
    reasons.append(f"RSI={momentum['rsi']:.1f}" if momentum["rsi"] else "RSI=N/A")
    reasons.append(f"rvol={volume['rvol']:.1f}x" if volume["rvol"] else "rvol=N/A")
    if volume.get("capitulation"):
        reasons.append("CAPITULATION")
    if volume.get("volume_exhaustion"):
        reasons.append("vol_exhaustion")

    # Multi-timeframe alignment adjustment
    tf_alignment_score: float | None = None
    if higher_tf_bars:
        from signals.timeframe import (
            check_timeframe_alignment,
            compute_timeframe_confidence_adjustment,
        )

        alignment = check_timeframe_alignment(higher_tf_bars)
        tf_adj = compute_timeframe_confidence_adjustment(alignment, direction)
        confidence = max(0.0, min(confidence + tf_adj, 1.0))
        tf_alignment_score = alignment["alignment_score"]
        reasons.append(f"tf_align={tf_alignment_score:.2f}")
        if alignment["dominant_direction"]:
            reasons.append(f"tf_dom={alignment['dominant_direction']}")

    if confidence < MIN_CONFIDENCE:
        return None

    position_size_factor = compute_position_size_factor(volume["conviction"])

    symbol = bars[-1].get("symbol", "")
    return Signal(
        symbol=symbol,
        direction=direction,
        entry_price=entry_price,
        stop_price=round(stop_price, 2),
        target_price=round(target_price, 2),
        confidence=round(confidence, 3),
        score=round(confidence, 3),
        regime=regime,
        sector=sector,
        signal_type=signal_type,
        reason="; ".join(reasons),
        timestamp=bars[-1].get("timestamp", int(time.time())),
        volume_conviction=volume["conviction"],
        position_size_factor=position_size_factor,
        timeframe_alignment=tf_alignment_score,
        atr=round(atr_val, 4),
    )


def _last_valid(series: list, offset: int = 0) -> float | None:
    """Get last non-None value from a series, with optional offset from end."""
    idx = len(series) - 1 - offset
    while idx >= 0:
        if series[idx] is not None:
            return series[idx]
        idx -= 1
    return None
