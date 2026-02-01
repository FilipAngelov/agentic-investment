"""Stock screener: candidate ranking with sector + regime scoring."""

from __future__ import annotations

import time

from config.sectors import BENCHMARK_SPY, ETF_TO_SECTOR, SECTOR_ETF_SYMBOLS
from scanner.regime import RegimeDetector
from scanner.screener import (
    MarketScanner,
    SCAN_HIGH_52W,
    SCAN_LOW_52W,
    SCAN_TOP_GAINERS,
    SCAN_TOP_LOSERS,
)
from scanner.sectors import SectorTracker

# Direction inference scan sets
_LONG_SCANS = {SCAN_TOP_GAINERS, SCAN_HIGH_52W}
_SHORT_SCANS = {SCAN_TOP_LOSERS, SCAN_LOW_52W}

# Symbols to exclude from candidate output
_EXCLUDED_SYMBOLS = set(SECTOR_ETF_SYMBOLS) | {BENCHMARK_SPY}

# Scoring weights
_WEIGHTS = {
    "scan_presence": 0.30,
    "scan_rank": 0.20,
    "sector_momentum": 0.20,
    "sector_acceleration": 0.10,
    "sector_rs": 0.20,
}


class CandidateRanker:
    """Score and rank scanner candidates using sector + regime context."""

    def __init__(
        self,
        scanner: MarketScanner,
        sector_tracker: SectorTracker,
        regime_detector: RegimeDetector,
    ) -> None:
        self._scanner = scanner
        self._sector_tracker = sector_tracker
        self._regime_detector = regime_detector
        self._watchlist: list[dict] = []
        self._last_rank_time: float = 0.0

    def rank_candidates(
        self, min_scans: int = 1, min_score: float = 0.0
    ) -> list[dict]:
        """Score, filter, and rank scanner candidates."""
        raw = self._scanner.get_combined_candidates(min_scans)
        scored: list[dict] = []
        for c in raw:
            if c["symbol"] in _EXCLUDED_SYMBOLS:
                continue
            enriched = self.compute_candidate_score(c)
            if enriched["adjusted_score"] >= min_score:
                scored.append(enriched)

        scored.sort(key=lambda x: x["adjusted_score"], reverse=True)
        for i, entry in enumerate(scored, 1):
            entry["rank"] = i

        self._watchlist = scored
        self._last_rank_time = time.time()
        return scored

    def compute_candidate_score(self, candidate: dict) -> dict:
        """Compute 5-component composite + regime-adjusted score."""
        scan_count = candidate.get("scan_count", 1)
        scans = candidate.get("scans", [])
        sector = candidate.get("sector")

        # Direction
        direction = self.infer_direction(candidate)

        # Sector context
        ctx = self.get_sector_context(sector)

        # Components
        scan_presence = min(scan_count / 4, 1.0)

        avg_rank = candidate.get("rank", 12)
        scan_rank = max(1 - (avg_rank / 25), 0.0)

        mom = ctx["momentum_score"]
        sector_momentum = min(max(mom * 10 + 0.5, 0), 1) if mom is not None else 0.5

        sector_acceleration = 1.0 if ctx["accelerating"] else 0.0

        rs = ctx["relative_strength"]
        sector_rs = min(rs / 2.0, 1.0) if rs is not None else 0.5

        composite = (
            _WEIGHTS["scan_presence"] * scan_presence
            + _WEIGHTS["scan_rank"] * scan_rank
            + _WEIGHTS["sector_momentum"] * sector_momentum
            + _WEIGHTS["sector_acceleration"] * sector_acceleration
            + _WEIGHTS["sector_rs"] * sector_rs
        )

        adjusted = self.apply_regime_adjustment(composite, direction)

        regime = self._regime_detector.get_current_regime()
        regime_factor = self._regime_detector.get_regime_factor(direction or "LONG")

        return {
            "symbol": candidate["symbol"],
            "composite_score": composite,
            "adjusted_score": adjusted,
            "direction": direction,
            "scan_count": scan_count,
            "scans": scans,
            "sector": sector,
            "sector_momentum": mom,
            "sector_accelerating": ctx["accelerating"],
            "sector_rs": rs,
            "regime": regime,
            "regime_factor": regime_factor,
            "rank": 0,
            # pass through
            "con_id": candidate.get("con_id"),
            "exchange": candidate.get("exchange"),
            "industry": candidate.get("industry"),
            "category": candidate.get("category"),
        }

    def infer_direction(self, candidate: dict) -> str | None:
        """LONG if mostly bullish scans, SHORT if bearish, None if tie."""
        scans = set(candidate.get("scans", []))
        long_count = len(scans & _LONG_SCANS)
        short_count = len(scans & _SHORT_SCANS)
        if long_count > short_count:
            return "LONG"
        if short_count > long_count:
            return "SHORT"
        return None

    def get_sector_context(self, sector_name: str | None) -> dict:
        """Return momentum/acceleration/RS for a sector, or neutral defaults."""
        if sector_name is not None:
            info = self._sector_tracker.get_sector_momentum(sector_name)
            if info is not None:
                return {
                    "momentum_score": info.get("momentum_score"),
                    "accelerating": self._sector_tracker.is_sector_accelerating(sector_name),
                    "relative_strength": info.get("relative_strength"),
                }
        return {
            "momentum_score": None,
            "accelerating": False,
            "relative_strength": None,
        }

    def apply_regime_adjustment(
        self, base_score: float, direction: str | None
    ) -> float:
        """Multiply by regime factor, clamp to [0, 1]."""
        factor = self._regime_detector.get_regime_factor(direction or "LONG")
        return min(max(base_score * factor, 0.0), 1.0)

    def get_watchlist(self) -> list[dict]:
        """Return last computed watchlist."""
        return self._watchlist


# ---------------------------------------------------------------------------
# Module-level wrappers
# ---------------------------------------------------------------------------


def rank_candidates(ranker: CandidateRanker, **kwargs) -> list[dict]:
    return ranker.rank_candidates(**kwargs)


def get_watchlist(ranker: CandidateRanker) -> list[dict]:
    return ranker.get_watchlist()
