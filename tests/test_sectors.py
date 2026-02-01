"""Tests for SectorTracker."""

import pytest

from config.sectors import SECTOR_ETFS, BENCHMARK_SPY
from scanner.sectors import SectorTracker


# Helper: generate linearly increasing prices
def _linear_prices(start: float, step: float, n: int) -> list[float]:
    return [start + step * i for i in range(n)]


def _timestamps(n: int) -> list[int]:
    return list(range(1000, 1000 + n))


@pytest.fixture
def tracker() -> SectorTracker:
    return SectorTracker()


@pytest.fixture
def loaded_tracker() -> SectorTracker:
    """Tracker with 65 bars for all 11 ETFs + SPY."""
    t = SectorTracker()
    ts = _timestamps(65)
    for i, (sector, etf) in enumerate(SECTOR_ETFS.items()):
        prices = _linear_prices(100.0, 0.1 * (i + 1), 65)
        t.ingest_bars(etf, prices, ts)
    t.ingest_bars(BENCHMARK_SPY, _linear_prices(400.0, 0.5, 65), ts)
    return t


# 1
def test_compute_momentum_basic(tracker):
    closes = [100 + 2 * i for i in range(10)]
    tracker.ingest_bars("XLK", closes, _timestamps(10))
    mom = tracker.compute_momentum("XLK")
    assert mom is not None
    roc5 = (118 - 108) / 108
    expected = 0.5 * roc5
    assert abs(mom - expected) < 1e-9


# 2
def test_compute_momentum_insufficient_history(tracker):
    tracker.ingest_bars("XLK", [100.0, 101.0, 102.0], _timestamps(3))
    assert tracker.compute_momentum("XLK") is None


# 3
def test_compute_relative_strength(tracker):
    ts = _timestamps(25)
    sector_closes = _linear_prices(100.0, 1.0, 25)
    spy_closes = _linear_prices(400.0, 2.0, 25)
    tracker.ingest_bars("XLK", sector_closes, ts)
    tracker.ingest_bars(BENCHMARK_SPY, spy_closes, ts)
    rs = tracker.compute_relative_strength("XLK")
    assert rs is not None
    sector_ret = (sector_closes[-1] - sector_closes[-21]) / sector_closes[-21]
    spy_ret = (spy_closes[-1] - spy_closes[-21]) / spy_closes[-21]
    assert abs(rs - sector_ret / spy_ret) < 1e-9


# 4
def test_get_sector_rankings_sorted(loaded_tracker):
    rankings = loaded_tracker.get_sector_rankings()
    scores = [r["momentum_score"] for r in rankings]
    assert scores == sorted(scores, reverse=True)


# 5
def test_get_sector_rankings_all_sectors(loaded_tracker):
    rankings = loaded_tracker.get_sector_rankings()
    assert len(rankings) == 11
    sectors = {r["sector"] for r in rankings}
    assert sectors == set(SECTOR_ETFS.keys())


# 6
def test_detect_rotation_inflow(tracker):
    ts = _timestamps(25)
    etfs = list(SECTOR_ETFS.values())
    for i, etf in enumerate(etfs):
        prices = _linear_prices(100.0, 0.1 * (i + 1), 25)
        tracker.ingest_bars(etf, prices, ts)
    base = [100.0] * 4 + [130.0] + [100.0] * 15 + [100.0, 105.0, 110.0, 118.0, 130.0]
    tracker.ingest_bars(etfs[0], base, ts)
    rotations = tracker.detect_rotation()
    inflows = [r for r in rotations if r["direction"] == "inflow"]
    assert len(inflows) >= 1


# 7
def test_detect_rotation_no_change(tracker):
    ts = _timestamps(25)
    for etf in SECTOR_ETFS.values():
        tracker.ingest_bars(etf, _linear_prices(100.0, 0.5, 25), ts)
    rotations = tracker.detect_rotation()
    assert rotations == []


# 8
def test_is_sector_accelerating_true(tracker):
    ts = _timestamps(25)
    prices = [100.0] * 5 + [95.0] * 15 + [96.0, 98.0, 100.0, 103.0, 108.0]
    tracker.ingest_bars("XLK", prices, ts)
    assert tracker.is_sector_accelerating("Technology") is True


# 9
def test_is_sector_accelerating_false(tracker):
    ts = _timestamps(25)
    prices = [100.0] * 5 + [120.0] * 15 + [120.0, 120.0, 120.0, 120.0, 121.0]
    tracker.ingest_bars("XLK", prices, ts)
    assert tracker.is_sector_accelerating("Technology") is False


# 10
async def test_save_snapshots(loaded_tracker, db):
    await loaded_tracker.save_snapshots(db)

    count = await db.fetchval("SELECT COUNT(*) FROM sector_snapshots")
    assert count == 11

    rows = await db.fetch("SELECT sector, price FROM sector_snapshots")
    sectors = {r["sector"] for r in rows}
    assert sectors == set(SECTOR_ETFS.keys())


# 11
def test_get_sector_momentum_single(loaded_tracker):
    result = loaded_tracker.get_sector_momentum("Technology")
    assert result is not None
    assert result["sector"] == "Technology"
    assert result["etf"] == "XLK"
    assert "momentum_score" in result


# 12
def test_get_sector_momentum_unknown(loaded_tracker):
    assert loaded_tracker.get_sector_momentum("Imaginary Sector") is None


# 13
def test_change_pct_calculation(tracker):
    tracker.ingest_bars("XLK", [100.0, 105.0], _timestamps(2))
    rankings = tracker.get_sector_rankings()
    tech = next(r for r in rankings if r["etf"] == "XLK")
    assert tech["change_pct"] is not None
    assert abs(tech["change_pct"] - 0.05) < 1e-9
