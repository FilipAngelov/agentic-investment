"""Composite signal scoring: blend technical, catalyst, sector, and expected-move signals."""

from __future__ import annotations

from data.models import Catalyst, DirectionType, RegimeType, Signal, Bar
from scanner.news import CatalystEngine
from signals.expected_move import ExpectedMove, calculate_expected_move

# ---------------------------------------------------------------------------
# Weights (must sum to 1.0)
# ---------------------------------------------------------------------------
W_TECHNICAL = 0.40
W_CATALYST = 0.25
W_SECTOR = 0.20
W_EM = 0.15

MIN_COMPOSITE_SCORE = 0.35


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def normalize_catalyst(css: float, direction: DirectionType) -> float:
    """Normalize catalyst strength score to 0-1.

    css=0 → 0.5 (neutral, don't penalize absence).
    """
    if css == 0.0:
        return 0.5
    if direction == "LONG":
        return _clamp(css / 5.0)
    # SHORT: negative catalyst is good
    return _clamp(-css / 5.0)


def normalize_sector(
    momentum: float | None,
    rs: float | None,
    accelerating: bool,
) -> float:
    """Normalize sector momentum/RS/acceleration to 0-1."""
    mom_n = _clamp(momentum * 10 + 0.5) if momentum is not None else 0.5
    rs_n = _clamp(rs / 2.0) if rs is not None else 0.5
    accel = 1.0 if accelerating else 0.0
    return 0.4 * mom_n + 0.4 * rs_n + 0.2 * accel


def normalize_expected_move(em_pct: float | None) -> float:
    """Map EM % to 0-1 opportunity score. 5% daily → 1.0."""
    if em_pct is None:
        return 0.5
    return _clamp(em_pct / 0.05)


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------


def score_signal(
    signal: Signal,
    css: float,
    sector_momentum: float | None,
    sector_rs: float | None,
    sector_accelerating: bool,
    em: ExpectedMove | None,
) -> Signal:
    """Enrich a Signal with a composite score. Returns a copy."""
    tech = signal.confidence
    catalyst = normalize_catalyst(css, signal.direction)
    sector = normalize_sector(sector_momentum, sector_rs, sector_accelerating)
    em_component = normalize_expected_move(em.em_pct if em else None)

    composite = (
        W_TECHNICAL * tech
        + W_CATALYST * catalyst
        + W_SECTOR * sector
        + W_EM * em_component
    )

    return signal.model_copy(
        update={
            "score": round(composite, 4),
            "expected_move_pct": em.em_pct if em else None,
        }
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

_catalyst_engine = CatalystEngine()


def score_candidate(
    signal: Signal,
    stock_bars: list[Bar],
    benchmark_bars: list[Bar],
    catalysts: list[Catalyst],
    sector_tracker_info: dict | None,
    regime: RegimeType,
) -> Signal:
    """Convenience: compute all components and return enriched Signal."""
    css = _catalyst_engine.get_catalyst_strength(catalysts)

    em = calculate_expected_move(
        stock_bars, benchmark_bars, css, regime, signal.direction
    )

    if sector_tracker_info:
        s_mom = sector_tracker_info.get("momentum")
        s_rs = sector_tracker_info.get("relative_strength")
        s_accel = sector_tracker_info.get("accelerating", False)
    else:
        s_mom = None
        s_rs = None
        s_accel = False

    return score_signal(signal, css, s_mom, s_rs, s_accel, em)


# ---------------------------------------------------------------------------
# Threshold gate
# ---------------------------------------------------------------------------


def passes_score_threshold(signal: Signal) -> bool:
    """Check if signal's composite score meets the minimum."""
    return signal.score >= MIN_COMPOSITE_SCORE
