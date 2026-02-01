"""Multi-timeframe alignment: confirm signals against higher timeframes."""

from __future__ import annotations

from data.indicators import macd, rsi, sma


def _last_valid(series: list, offset: int = 0) -> float | None:
    idx = len(series) - 1 - offset
    while idx >= 0:
        if series[idx] is not None:
            return series[idx]
        idx -= 1
    return None


def _analyse_timeframe(bars: list[dict]) -> dict | None:
    """Compute trend, MACD direction, and RSI zone for a single timeframe."""
    if len(bars) < 51:
        return None

    closes = [b["close"] for b in bars]

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    s20 = _last_valid(sma20)
    s50 = _last_valid(sma50)

    if s20 is not None and s50 is not None:
        if s20 > s50 * 1.001:
            trend = "up"
        elif s20 < s50 * 0.999:
            trend = "down"
        else:
            trend = "flat"
    else:
        trend = "flat"

    _, _, macd_hist = macd(closes)
    h0 = _last_valid(macd_hist)
    h1 = _last_valid(macd_hist, offset=1)
    macd_rising = h0 is not None and h1 is not None and h0 > h1

    rsi_vals = rsi(closes)
    rsi_val = _last_valid(rsi_vals)
    if rsi_val is not None:
        if rsi_val >= 70:
            rsi_signal = "overbought"
        elif rsi_val <= 30:
            rsi_signal = "oversold"
        else:
            rsi_signal = "neutral"
    else:
        rsi_signal = "neutral"

    return {"trend": trend, "macd_rising": macd_rising, "rsi_signal": rsi_signal}


def check_timeframe_alignment(bars_by_tf: dict[str, list[dict]]) -> dict:
    """Analyse multiple timeframes and return alignment info.

    Parameters
    ----------
    bars_by_tf : dict mapping timeframe label (e.g. "15m", "1d") to bar list.

    Returns
    -------
    dict with keys: aligned, alignment_score, details, dominant_direction.
    """
    details: dict[str, dict] = {}
    for tf, bars in bars_by_tf.items():
        result = _analyse_timeframe(bars)
        if result is not None:
            details[tf] = result

    if not details:
        return {
            "aligned": False,
            "alignment_score": 0.0,
            "details": {},
            "dominant_direction": None,
        }

    up_count = sum(1 for d in details.values() if d["trend"] == "up")
    down_count = sum(1 for d in details.values() if d["trend"] == "down")
    total = len(details)

    if up_count == total:
        dominant = "LONG"
        score = 1.0
        aligned = True
    elif down_count == total:
        dominant = "SHORT"
        score = 1.0
        aligned = True
    else:
        dominant = "LONG" if up_count > down_count else ("SHORT" if down_count > up_count else None)
        score = max(up_count, down_count) / total
        aligned = False

    return {
        "aligned": aligned,
        "alignment_score": round(score, 2),
        "details": details,
        "dominant_direction": dominant,
    }


def compute_timeframe_confidence_adjustment(alignment: dict, direction: str) -> float:
    """Map alignment result to a confidence modifier for the given signal direction.

    Returns a float to add to the signal's confidence score.
    """
    if not alignment["details"]:
        return 0.0

    expected_trend = "up" if direction == "LONG" else "down"
    opposed_trend = "down" if direction == "LONG" else "up"

    confirming = sum(
        1 for d in alignment["details"].values() if d["trend"] == expected_trend
    )
    opposed = sum(
        1 for d in alignment["details"].values() if d["trend"] == opposed_trend
    )
    total = len(alignment["details"])

    # Higher TF actively opposed
    if opposed > 0 and confirming == 0:
        return -0.15

    if confirming == total:
        return 0.15
    if confirming / total >= 0.5:
        return 0.05
    return -0.10
