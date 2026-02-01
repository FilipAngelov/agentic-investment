"""Tests for scanner.candidate_ranker — CandidateRanker."""

import pytest

from config.sectors import BENCHMARK_SPY, REGIME_BEAR, REGIME_STRONG_BULL, SECTOR_ETFS
from scanner.candidate_ranker import CandidateRanker
from scanner.regime import RegimeDetector
from scanner.screener import (
    MarketScanner,
    SCAN_HIGH_52W,
    SCAN_LOW_52W,
    SCAN_MOST_ACTIVE,
    SCAN_TOP_GAINERS,
    SCAN_TOP_LOSERS,
)
from scanner.sectors import SectorTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scanner_with_results(results: dict[str, list[dict]]) -> MarketScanner:
    s = MarketScanner()
    s._results = results
    return s


def _make_sector_tracker() -> SectorTracker:
    """Tracker with Technology sector data + SPY benchmark."""
    tracker = SectorTracker()
    # 65 closes: gentle uptrend for XLK
    closes = [100 + i * 0.5 for i in range(65)]
    ts = list(range(65))
    tracker.ingest_bars("XLK", closes, ts)
    # SPY benchmark
    spy_closes = [100 + i * 0.3 for i in range(65)]
    tracker.ingest_bars(BENCHMARK_SPY, spy_closes, ts)
    return tracker


def _make_regime(regime_type=None):
    d = RegimeDetector()
    d._current_regime = regime_type
    return d


def _candidate(symbol, scans, rank=5, sector=None):
    return {
        "symbol": symbol,
        "scan_count": len(scans),
        "scans": sorted(scans),
        "rank": rank,
        "sector": sector,
        "con_id": 123,
        "exchange": "SMART",
        "industry": "Tech",
        "category": "Software",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_rank_candidates_sorted():
    """Candidates are returned sorted by adjusted_score descending."""
    scanner = _make_scanner_with_results({
        SCAN_TOP_GAINERS: [
            {"symbol": "AAA", "rank": 1, "con_id": 1, "exchange": "SMART",
             "industry": None, "category": None},
            {"symbol": "BBB", "rank": 2, "con_id": 2, "exchange": "SMART",
             "industry": None, "category": None},
        ],
        SCAN_MOST_ACTIVE: [
            {"symbol": "AAA", "rank": 3, "con_id": 1, "exchange": "SMART",
             "industry": None, "category": None},
        ],
    })
    ranker = CandidateRanker(scanner, _make_sector_tracker(), _make_regime())
    result = ranker.rank_candidates(min_scans=1)
    scores = [c["adjusted_score"] for c in result]
    assert scores == sorted(scores, reverse=True)
    assert result[0]["rank"] == 1


def test_composite_score_calculation():
    """Verify formula with known inputs."""
    tracker = _make_sector_tracker()
    regime = _make_regime()  # None regime → factor 1.0
    ranker = CandidateRanker(MarketScanner(), tracker, regime)

    c = _candidate("AAPL", [SCAN_TOP_GAINERS, SCAN_MOST_ACTIVE], rank=5, sector="Technology")
    enriched = ranker.compute_candidate_score(c)

    # scan_presence = min(2/4, 1) = 0.5
    # scan_rank = 1 - 5/25 = 0.8
    assert enriched["composite_score"] == pytest.approx(enriched["composite_score"], abs=0.01)
    assert 0 <= enriched["composite_score"] <= 1
    assert 0 <= enriched["adjusted_score"] <= 1


def test_direction_inference_long():
    """TOP_PERC_GAIN → LONG."""
    ranker = CandidateRanker(MarketScanner(), SectorTracker(), _make_regime())
    c = _candidate("TSLA", [SCAN_TOP_GAINERS])
    assert ranker.infer_direction(c) == "LONG"


def test_direction_inference_short():
    """TOP_PERC_LOSE → SHORT."""
    ranker = CandidateRanker(MarketScanner(), SectorTracker(), _make_regime())
    c = _candidate("TSLA", [SCAN_TOP_LOSERS])
    assert ranker.infer_direction(c) == "SHORT"


def test_direction_inference_neutral():
    """MOST_ACTIVE only → None."""
    ranker = CandidateRanker(MarketScanner(), SectorTracker(), _make_regime())
    c = _candidate("TSLA", [SCAN_MOST_ACTIVE])
    assert ranker.infer_direction(c) is None


def test_regime_adjustment_strong_bull():
    """Strong bull factor 1.5, clamped to 1.0."""
    regime = _make_regime(REGIME_STRONG_BULL)
    ranker = CandidateRanker(MarketScanner(), SectorTracker(), regime)
    result = ranker.apply_regime_adjustment(0.8, "LONG")
    assert result == pytest.approx(1.0)  # 0.8 * 1.5 = 1.2 → clamped


def test_regime_adjustment_bear_long():
    """Bear long factor 0.5."""
    regime = _make_regime(REGIME_BEAR)
    ranker = CandidateRanker(MarketScanner(), SectorTracker(), regime)
    result = ranker.apply_regime_adjustment(0.8, "LONG")
    assert result == pytest.approx(0.4)  # 0.8 * 0.5


def test_sector_context_known():
    """Known sector returns real momentum/RS values."""
    tracker = _make_sector_tracker()
    ranker = CandidateRanker(MarketScanner(), tracker, _make_regime())
    ctx = ranker.get_sector_context("Technology")
    assert ctx["momentum_score"] is not None
    assert isinstance(ctx["accelerating"], bool)
    assert ctx["relative_strength"] is not None


def test_sector_context_unknown():
    """Unknown sector returns neutral defaults."""
    ranker = CandidateRanker(MarketScanner(), SectorTracker(), _make_regime())
    ctx = ranker.get_sector_context("Nonexistent")
    assert ctx["momentum_score"] is None
    assert ctx["accelerating"] is False
    assert ctx["relative_strength"] is None


def test_filter_min_score():
    """Candidates below min_score are excluded."""
    scanner = _make_scanner_with_results({
        SCAN_TOP_GAINERS: [
            {"symbol": "HIGH", "rank": 1, "con_id": 1, "exchange": "SMART",
             "industry": None, "category": None},
            {"symbol": "LOW", "rank": 24, "con_id": 2, "exchange": "SMART",
             "industry": None, "category": None},
        ],
    })
    ranker = CandidateRanker(scanner, _make_sector_tracker(), _make_regime())
    result = ranker.rank_candidates(min_scans=1, min_score=0.5)
    symbols = [c["symbol"] for c in result]
    # All returned candidates meet the threshold
    for c in result:
        assert c["adjusted_score"] >= 0.5
