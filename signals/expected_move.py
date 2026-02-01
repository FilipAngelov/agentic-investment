"""Expected Move calculator: EM = CSS_multiplier × β × (HV₂₀ / √252) × regime_factor."""

from __future__ import annotations

import math
from dataclasses import dataclass

from config.sectors import (
    REGIME_BEAR,
    REGIME_BEAR_LONG_FACTOR,
    REGIME_FACTORS,
)
from data.indicators import historical_volatility
from data.models import Bar, RegimeType


# CSS anchor points: (css_value, multiplier)
_CSS_ANCHORS: list[tuple[float, float]] = [
    (1.0, 1.0),
    (2.0, 1.5),
    (3.0, 2.0),
    (4.0, 3.0),
    (5.0, 5.0),
]


def css_to_multiplier(css: float) -> float:
    """Map catalyst strength score to expected-move multiplier via linear interpolation."""
    css = abs(css)
    if css <= _CSS_ANCHORS[0][0]:
        return _CSS_ANCHORS[0][1]
    if css >= _CSS_ANCHORS[-1][0]:
        return _CSS_ANCHORS[-1][1]
    for i in range(len(_CSS_ANCHORS) - 1):
        x0, y0 = _CSS_ANCHORS[i]
        x1, y1 = _CSS_ANCHORS[i + 1]
        if x0 <= css <= x1:
            t = (css - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return _CSS_ANCHORS[-1][1]  # pragma: no cover


def compute_beta(
    stock_closes: list[float],
    benchmark_closes: list[float],
    period: int = 20,
) -> float | None:
    """Rolling beta over *period* bars. Returns latest value or None if insufficient data."""
    n = min(len(stock_closes), len(benchmark_closes))
    if n < period + 1:
        return None
    # Use the last period+1 prices → period returns
    s = stock_closes[n - period - 1 : n]
    b = benchmark_closes[n - period - 1 : n]
    s_ret = [(s[i] / s[i - 1]) - 1.0 for i in range(1, len(s))]
    b_ret = [(b[i] / b[i - 1]) - 1.0 for i in range(1, len(b))]
    mean_b = sum(b_ret) / period
    mean_s = sum(s_ret) / period
    cov = sum((s_ret[i] - mean_s) * (b_ret[i] - mean_b) for i in range(period)) / period
    var_b = sum((b_ret[i] - mean_b) ** 2 for i in range(period)) / period
    if var_b == 0:
        return None
    return cov / var_b


def compute_expected_move(
    css: float,
    beta: float,
    hv20: float,
    regime_factor: float,
) -> float:
    """Return EM as a fraction (e.g. 0.025 = 2.5% expected daily move)."""
    daily_vol = hv20 / math.sqrt(252)
    return css_to_multiplier(abs(css)) * beta * daily_vol * regime_factor


@dataclass
class ExpectedMove:
    em_pct: float
    css: float
    css_multiplier: float
    beta: float
    hv20: float
    regime_factor: float


def _get_regime_factor(regime: RegimeType, direction: str) -> float:
    if regime == REGIME_BEAR and direction == "LONG":
        return REGIME_BEAR_LONG_FACTOR
    return REGIME_FACTORS[regime]


def calculate_expected_move(
    stock_bars: list[Bar],
    benchmark_bars: list[Bar],
    css: float,
    regime: RegimeType,
    direction: str,
) -> ExpectedMove | None:
    """Orchestrate EM calculation from raw bars, CSS, regime, and direction."""
    stock_closes = [b.close for b in stock_bars]
    bench_closes = [b.close for b in benchmark_bars]

    hv_series = historical_volatility(stock_closes, 20)
    # Get latest valid HV
    hv20: float | None = None
    for v in reversed(hv_series):
        if v is not None:
            hv20 = v
            break
    if hv20 is None:
        return None

    beta = compute_beta(stock_closes, bench_closes)
    if beta is None:
        return None

    regime_factor = _get_regime_factor(regime, direction)
    em = compute_expected_move(css, beta, hv20, regime_factor)
    return ExpectedMove(
        em_pct=em,
        css=css,
        css_multiplier=css_to_multiplier(abs(css)),
        beta=beta,
        hv20=hv20,
        regime_factor=regime_factor,
    )
