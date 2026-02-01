"""Tests for MarketScanner."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scanner.screener import (
    ALL_SCANS,
    SCAN_HOT_BY_VOLUME,
    SCAN_MOST_ACTIVE,
    SCAN_TOP_GAINERS,
    SCAN_TOP_LOSERS,
    MarketScanner,
)
from scanner.sectors import SectorTracker


def _make_scan_data(symbol: str, rank: int = 0) -> SimpleNamespace:
    """Build a fake ScanData-like object."""
    contract = SimpleNamespace(
        symbol=symbol, conId=rank + 1000, exchange="SMART", secType="STK"
    )
    details = SimpleNamespace(contract=contract, industry="Tech", category="Software")
    return SimpleNamespace(
        rank=rank,
        contractDetails=details,
        distance="1.5",
        benchmark="SPY",
        projection="10.0",
        legsStr="",
    )


def _mock_ib(results: list | None = None) -> AsyncMock:
    """Return a mock IB with reqScannerDataAsync returning results."""
    ib = AsyncMock()
    ib.reqScannerDataAsync.return_value = results if results is not None else []
    return ib


@pytest.fixture
def scanner() -> MarketScanner:
    return MarketScanner()


# 1
async def test_run_scan_basic(scanner):
    raw = [_make_scan_data("AAPL", 0), _make_scan_data("MSFT", 1)]
    ib = _mock_ib(raw)
    results = await scanner.run_scan(ib, SCAN_TOP_GAINERS)
    assert len(results) == 2
    assert results[0]["symbol"] == "AAPL"
    assert results[1]["symbol"] == "MSFT"
    assert results[0]["scan_code"] == SCAN_TOP_GAINERS


# 2
async def test_run_scan_empty(scanner):
    ib = _mock_ib([])
    results = await scanner.run_scan(ib, SCAN_TOP_GAINERS)
    assert results == []


# 3
async def test_run_all_scans_populates_all(scanner):
    raw = [_make_scan_data("AAPL", 0)]
    ib = _mock_ib(raw)
    all_results = await scanner.run_all_scans(ib)
    assert len(all_results) == 6
    for scan_code in ALL_SCANS:
        assert scan_code in all_results
        assert len(all_results[scan_code]) == 1


# 4
async def test_get_top_gainers(scanner):
    raw = [_make_scan_data("TSLA", 0)]
    ib = _mock_ib(raw)
    await scanner.run_scan(ib, SCAN_TOP_GAINERS)
    assert len(scanner.get_top_gainers()) == 1
    assert scanner.get_top_gainers()[0]["symbol"] == "TSLA"


# 5
async def test_get_top_losers(scanner):
    raw = [_make_scan_data("META", 0)]
    ib = _mock_ib(raw)
    await scanner.run_scan(ib, SCAN_TOP_LOSERS)
    assert len(scanner.get_top_losers()) == 1
    assert scanner.get_top_losers()[0]["symbol"] == "META"


# 6
async def test_get_unusual_volume(scanner):
    raw = [_make_scan_data("NVDA", 0)]
    ib = _mock_ib(raw)
    await scanner.run_scan(ib, SCAN_HOT_BY_VOLUME)
    assert len(scanner.get_unusual_volume()) == 1


# 7
async def test_get_scan_results_specific(scanner):
    raw = [_make_scan_data("GOOG", 0)]
    ib = _mock_ib(raw)
    await scanner.run_scan(ib, SCAN_MOST_ACTIVE)
    results = scanner.get_scan_results(SCAN_MOST_ACTIVE)
    assert isinstance(results, list)
    assert len(results) == 1


# 8
async def test_get_scan_results_all(scanner):
    raw = [_make_scan_data("AAPL", 0)]
    ib = _mock_ib(raw)
    await scanner.run_all_scans(ib)
    results = scanner.get_scan_results()
    assert isinstance(results, dict)
    assert len(results) == 6


# 9
async def test_get_combined_candidates_dedup(scanner):
    # Put same symbol in two different scans
    scanner._results[SCAN_TOP_GAINERS] = [
        {"symbol": "AAPL", "scan_code": SCAN_TOP_GAINERS, "rank": 0},
        {"symbol": "MSFT", "scan_code": SCAN_TOP_GAINERS, "rank": 1},
    ]
    scanner._results[SCAN_MOST_ACTIVE] = [
        {"symbol": "AAPL", "scan_code": SCAN_MOST_ACTIVE, "rank": 0},
        {"symbol": "GOOG", "scan_code": SCAN_MOST_ACTIVE, "rank": 1},
    ]
    candidates = scanner.get_combined_candidates(min_scans=2)
    assert len(candidates) == 1
    assert candidates[0]["symbol"] == "AAPL"
    assert candidates[0]["scan_count"] == 2


# 10
def test_tag_sector():
    scanner = MarketScanner(sector_tracker=SectorTracker())
    assert scanner.tag_sector("XLK") == "Technology"
    assert scanner.tag_sector("XLF") == "Financials"
    assert scanner.tag_sector("AAPL") is None


# 11
async def test_pre_market_scan_mode(scanner):
    raw = [_make_scan_data("AAPL", 0)]
    ib = _mock_ib(raw)
    await scanner.run_scan(ib, SCAN_TOP_GAINERS, mode="pre_market")
    # Verify the subscription was built (scan succeeded)
    assert len(scanner.get_top_gainers()) == 1
    # Check that reqScannerDataAsync was called
    ib.reqScannerDataAsync.assert_called_once()


# 12
async def test_connection_error_handling(scanner):
    ib = AsyncMock()
    ib.reqScannerDataAsync.side_effect = ConnectionError("No connection")
    results = await scanner.run_scan(ib, SCAN_TOP_GAINERS)
    assert results == []
