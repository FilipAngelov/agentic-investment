"""Tests for RegimeDetector."""

import pytest
import aiosqlite

from data.store import SCHEMA_SQL
from scanner.regime import RegimeDetector


def _timestamps(n: int) -> list[int]:
    return list(range(1000, 1000 + n))


def _make_detector(spy_last: float, vix_last: float, sma20_target: float | None = None, sma50_target: float | None = None) -> RegimeDetector:
    """Build a detector with 60 SPY bars and 60 VIX bars.

    SPY bars: first 50 bars set so SMA50 ~ sma50_target, last 10 approach spy_last.
    For simplicity, we construct flat prices at sma50_target then override the tail.
    """
    d = RegimeDetector()
    ts = _timestamps(60)

    # Default SMA targets to spy_last if not specified
    if sma50_target is None:
        sma50_target = spy_last
    if sma20_target is None:
        sma20_target = spy_last

    # Build 60 SPY closes that produce desired SMAs
    # Strategy: 40 bars at a base price, then 20 bars at sma20_target
    # SMA50 = (30 * base + 20 * sma20_target) / 50 = sma50_target
    # => base = (sma50_target * 50 - 20 * sma20_target) / 30
    base = (sma50_target * 50 - 20 * sma20_target) / 30
    spy_prices = [base] * 40 + [sma20_target] * 19 + [spy_last]
    d.ingest_spy(spy_prices, ts)

    vix_prices = [vix_last] * 60
    d.ingest_vix(vix_prices, ts)
    return d


# 1
def test_classify_strong_bull():
    # SPY=500, SMA20~490, SMA50~480, VIX=12
    # Need: SPY > SMA20 > SMA50 and VIX < 15
    d = RegimeDetector()
    ts = _timestamps(60)
    # 40 bars at 470, 19 bars at 490, last bar at 500
    # SMA20 = (19*490 + 500) / 20 = 490.5
    # SMA50 = (30*470 + 19*490 + 500) / 50 = (14100 + 9310 + 500)/50 = 478.2
    spy = [470.0] * 40 + [490.0] * 19 + [500.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([12.0] * 60, ts)
    regime = d.classify_regime()
    assert regime == "strong_bull"


# 2
def test_classify_bull():
    # SPY > SMA50, VIX between 15-20
    d = RegimeDetector()
    ts = _timestamps(60)
    # Flat at 450 for 40 bars, then 460 for 19 bars, last 470
    # SMA50 = (30*450 + 19*460 + 470)/50 = (13500+8740+470)/50 = 454.2
    # SMA20 = (19*460 + 470)/20 = 460.5
    # SPY=470 > SMA20=460.5 > SMA50=454.2, but VIX=17 >= 15 → not strong_bull → bull
    spy = [450.0] * 40 + [460.0] * 19 + [470.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([17.0] * 60, ts)
    regime = d.classify_regime()
    assert regime == "bull"


# 3
def test_classify_choppy():
    # VIX=22, SPY between SMAs → choppy fallback
    d = RegimeDetector()
    ts = _timestamps(60)
    # SMA50 high, SMA20 low, SPY in between → choppy
    # 40 bars at 500, 19 bars at 460, last 470
    # SMA50 = (30*500 + 19*460 + 470)/50 = (15000+8740+470)/50 = 484.2
    # SMA20 = (19*460 + 470)/20 = 460.5
    # SPY=470 < SMA50=484.2, but VIX=22 < 25 → not bear → choppy
    spy = [500.0] * 40 + [460.0] * 19 + [470.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([22.0] * 60, ts)
    regime = d.classify_regime()
    assert regime == "choppy"


# 4
def test_classify_bear():
    # SPY < SMA50, VIX >= 25
    d = RegimeDetector()
    ts = _timestamps(60)
    # 40 bars at 500, 19 bars at 460, last 450
    # SMA50 = (30*500 + 19*460 + 450)/50 = (15000+8740+450)/50 = 483.8
    # SPY=450 < SMA50=483.8, VIX=30 >= 25 → bear
    spy = [500.0] * 40 + [460.0] * 19 + [450.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([30.0] * 60, ts)
    regime = d.classify_regime()
    assert regime == "bear"


# 5
def test_classify_crisis():
    # VIX > 35 → crisis regardless of SPY
    d = RegimeDetector()
    ts = _timestamps(60)
    spy = [500.0] * 60
    d.ingest_spy(spy, ts)
    d.ingest_vix([40.0] * 60, ts)
    regime = d.classify_regime()
    assert regime == "crisis"


# 6
def test_classify_insufficient_data():
    d = RegimeDetector()
    d.ingest_spy([100.0] * 10, _timestamps(10))
    d.ingest_vix([20.0] * 10, _timestamps(10))
    assert d.classify_regime() is None


# 7
def test_regime_factor_strong_bull():
    d = RegimeDetector()
    ts = _timestamps(60)
    spy = [470.0] * 40 + [490.0] * 19 + [500.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([12.0] * 60, ts)
    d.classify_regime()
    assert d.get_regime_factor() == 1.5


# 8
def test_regime_factor_bear_long():
    d = RegimeDetector()
    ts = _timestamps(60)
    spy = [500.0] * 40 + [460.0] * 19 + [450.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([30.0] * 60, ts)
    d.classify_regime()
    assert d.get_regime_factor("LONG") == 0.5


# 9
def test_regime_factor_bear_short():
    d = RegimeDetector()
    ts = _timestamps(60)
    spy = [500.0] * 40 + [460.0] * 19 + [450.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([30.0] * 60, ts)
    d.classify_regime()
    assert d.get_regime_factor("SHORT") == 0.8


# 10
def test_regime_factor_crisis():
    d = RegimeDetector()
    ts = _timestamps(60)
    d.ingest_spy([500.0] * 60, ts)
    d.ingest_vix([40.0] * 60, ts)
    d.classify_regime()
    assert d.get_regime_factor() == 0.3


# 11
def test_detect_regime_change():
    d = RegimeDetector()
    ts = _timestamps(60)
    # First classify as bull
    spy_bull = [450.0] * 40 + [460.0] * 19 + [470.0]
    d.ingest_spy(spy_bull, ts)
    d.ingest_vix([17.0] * 60, ts)
    d.classify_regime()

    # Now classify as bear
    spy_bear = [500.0] * 40 + [460.0] * 19 + [450.0]
    d.ingest_spy(spy_bear, ts)
    d.ingest_vix([30.0] * 60, ts)
    d.classify_regime()

    change = d.detect_regime_change()
    assert change is not None
    assert change["from_regime"] == "bull"
    assert change["to_regime"] == "bear"


# 12
def test_detect_no_change():
    d = RegimeDetector()
    ts = _timestamps(60)
    spy = [470.0] * 40 + [490.0] * 19 + [500.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([12.0] * 60, ts)
    d.classify_regime()
    d.classify_regime()  # same data → same regime
    assert d.detect_regime_change() is None


# 13
async def test_log_regime(tmp_path):
    d = RegimeDetector()
    ts = _timestamps(60)
    d.ingest_spy([500.0] * 60, ts)
    d.ingest_vix([12.0] * 60, ts)
    d.classify_regime()

    db_path = tmp_path / "test.db"
    db = await aiosqlite.connect(db_path)
    await db.executescript(SCHEMA_SQL)
    await db.commit()

    await d.log_regime(db)

    cursor = await db.execute("SELECT COUNT(*) FROM regime_log")
    count = (await cursor.fetchone())[0]
    assert count == 1

    cursor = await db.execute("SELECT regime, vix FROM regime_log")
    row = await cursor.fetchone()
    assert row[0] in ("strong_bull", "bull", "choppy", "bear", "crisis")
    assert row[1] == 12.0
    await db.close()


# 14
def test_spy_vs_sma_ratios():
    d = RegimeDetector()
    ts = _timestamps(60)
    # Flat at 500 → SMA20=500, SMA50=500, ratios=1.0
    d.ingest_spy([500.0] * 60, ts)
    sma = d.get_spy_vs_sma()
    assert sma["spy_price"] == 500.0
    assert sma["sma_20"] == 500.0
    assert sma["sma_50"] == 500.0
    assert abs(sma["spy_vs_20sma"] - 1.0) < 1e-9
    assert abs(sma["spy_vs_50sma"] - 1.0) < 1e-9
