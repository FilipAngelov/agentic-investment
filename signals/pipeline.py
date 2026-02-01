"""Signal generation pipeline: bars → technical signal → composite score → final Signal.

Single entry point for Phase 3 (execution) to call.
"""

from __future__ import annotations

from data.models import Bar, Catalyst, RegimeType, Signal
from signals.scoring import passes_score_threshold, score_candidate
from signals.technical import generate_technical_signal


def generate_signal(
    bars: list[dict],
    regime: RegimeType,
    stock_bars: list[Bar],
    benchmark_bars: list[Bar],
    catalysts: list[Catalyst],
    sector_tracker_info: dict | None = None,
    sector: str | None = None,
    direction_hint: str | None = None,
    higher_tf_bars: dict[str, list[dict]] | None = None,
) -> Signal | None:
    """Full pipeline: technical analysis → composite scoring → threshold gate.

    Returns an enriched Signal if it passes all checks, otherwise None.

    Parameters
    ----------
    bars : list[dict]
        Primary timeframe OHLCV bars (dicts with open/high/low/close/volume/timestamp/symbol).
    regime : RegimeType
        Current market regime.
    stock_bars : list[Bar]
        Daily Bar objects for the stock (used by EM calculator).
    benchmark_bars : list[Bar]
        Daily Bar objects for SPY (used by EM/beta calculator).
    catalysts : list[Catalyst]
        Recent catalysts for the symbol.
    sector_tracker_info : dict | None
        Dict with keys 'momentum', 'relative_strength', 'accelerating'.
    sector : str | None
        Sector label for the symbol.
    direction_hint : str | None
        If set, only produce signals in this direction.
    higher_tf_bars : dict | None
        Higher timeframe bars for multi-timeframe alignment.
    """
    # Step 1: Technical signal generation
    technical_signal = generate_technical_signal(
        bars,
        regime,
        direction_hint=direction_hint,
        sector=sector,
        higher_tf_bars=higher_tf_bars,
    )
    if technical_signal is None:
        return None

    # Step 2: Composite scoring (adds catalyst, sector, EM components)
    scored_signal = score_candidate(
        technical_signal,
        stock_bars,
        benchmark_bars,
        catalysts,
        sector_tracker_info,
        regime,
    )

    # Step 3: Threshold gate
    if not passes_score_threshold(scored_signal):
        return None

    return scored_signal
