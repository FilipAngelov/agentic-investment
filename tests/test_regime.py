"""Tests for RegimeDetector."""

import pytest

from scanner.regime import RegimeDetector


def _timestamps(n: int) -> list[int]:
    return list(range(1000, 1000 + n))


# 1
def test_classify_strong_bull():
    d = RegimeDetector()
    ts = _timestamps(60)
    spy = [470.0] * 40 + [490.0] * 19 + [500.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([12.0] * 60, ts)
    regime = d.classify_regime()
    assert regime == "strong_bull"


# 2
def test_classify_bull():
    d = RegimeDetector()
    ts = _timestamps(60)
    spy = [450.0] * 40 + [460.0] * 19 + [470.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([17.0] * 60, ts)
    regime = d.classify_regime()
    assert regime == "bull"


# 3
def test_classify_choppy():
    d = RegimeDetector()
    ts = _timestamps(60)
    spy = [500.0] * 40 + [460.0] * 19 + [470.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([22.0] * 60, ts)
    regime = d.classify_regime()
    assert regime == "choppy"


# 4
def test_classify_bear():
    d = RegimeDetector()
    ts = _timestamps(60)
    spy = [500.0] * 40 + [460.0] * 19 + [450.0]
    d.ingest_spy(spy, ts)
    d.ingest_vix([30.0] * 60, ts)
    regime = d.classify_regime()
    assert regime == "bear"


# 5
def test_classify_crisis():
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
    spy_bull = [450.0] * 40 + [460.0] * 19 + [470.0]
    d.ingest_spy(spy_bull, ts)
    d.ingest_vix([17.0] * 60, ts)
    d.classify_regime()

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
    d.classify_regime()
    assert d.detect_regime_change() is None


# 13
async def test_log_regime(db):
    d = RegimeDetector()
    ts = _timestamps(60)
    d.ingest_spy([500.0] * 60, ts)
    d.ingest_vix([12.0] * 60, ts)
    d.classify_regime()

    await d.log_regime(db)

    count = await db.fetchval("SELECT COUNT(*) FROM regime_log")
    assert count == 1

    row = await db.fetchrow("SELECT regime, vix FROM regime_log")
    assert row["regime"] in ("strong_bull", "bull", "choppy", "bear", "crisis")
    assert row["vix"] == 12.0


# 14
def test_spy_vs_sma_ratios():
    d = RegimeDetector()
    ts = _timestamps(60)
    d.ingest_spy([500.0] * 60, ts)
    sma = d.get_spy_vs_sma()
    assert sma["spy_price"] == 500.0
    assert sma["sma_20"] == 500.0
    assert sma["sma_50"] == 500.0
    assert abs(sma["spy_vs_20sma"] - 1.0) < 1e-9
    assert abs(sma["spy_vs_50sma"] - 1.0) < 1e-9
