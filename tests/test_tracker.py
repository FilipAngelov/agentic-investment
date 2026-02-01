"""Tests for portfolio.tracker.PositionTracker (task 4.1)."""

import pytest

from portfolio.tracker import PositionTracker


def _make_tracker_with_long(price: float = 100.0) -> PositionTracker:
    t = PositionTracker()
    t.open_position("AAPL", "LONG", 10, 100.0, 1000, 95.0, price, "Technology")
    return t


def _make_tracker_with_short(price: float = 50.0) -> PositionTracker:
    t = PositionTracker()
    t.open_position("TSLA", "SHORT", 20, 50.0, 2000, 55.0, price)
    return t


# --- Open position ---

def test_open_position_fields():
    t = _make_tracker_with_long(100.0)
    pos = t.get("AAPL")
    assert pos is not None
    assert pos.symbol == "AAPL"
    assert pos.direction == "LONG"
    assert pos.shares == 10
    assert pos.entry_price == 100.0
    assert pos.stop_price == 95.0
    assert pos.sector == "Technology"
    assert pos.unrealized_pnl == 0.0


# --- Update price (long) ---

def test_update_price_long_profit():
    t = _make_tracker_with_long()
    t.update_price("AAPL", 110.0)
    assert t.get("AAPL").unrealized_pnl == pytest.approx(100.0)


def test_update_price_long_loss():
    t = _make_tracker_with_long()
    t.update_price("AAPL", 90.0)
    assert t.get("AAPL").unrealized_pnl == pytest.approx(-100.0)


# --- Update price (short) ---

def test_update_price_short():
    t = _make_tracker_with_short()
    t.update_price("TSLA", 45.0)
    assert t.get("TSLA").unrealized_pnl == pytest.approx(100.0)  # (50-45)*20


# --- Batch update ---

def test_update_prices_batch():
    t = _make_tracker_with_long()
    t.open_position("MSFT", "LONG", 5, 200.0, 1001, 190.0, 200.0, "Technology")
    t.update_prices({"AAPL": 105.0, "MSFT": 210.0, "UNKNOWN": 1.0})
    assert t.get("AAPL").unrealized_pnl == pytest.approx(50.0)
    assert t.get("MSFT").unrealized_pnl == pytest.approx(50.0)


# --- Update stop ---

def test_update_stop():
    t = _make_tracker_with_long()
    t.update_stop("AAPL", 97.0)
    assert t.get("AAPL").stop_price == 97.0


# --- Reduce shares (partial exit) ---

def test_reduce_shares_partial():
    t = _make_tracker_with_long()
    pnl = t.reduce_shares("AAPL", 4, 110.0, 3000)
    assert pnl == pytest.approx(40.0)
    assert t.get("AAPL").shares == 6
    assert t.realized_pnl == pytest.approx(40.0)
    assert len(t.closed_trades) == 1
    ct = t.closed_trades[0]
    assert ct.shares == 4
    assert ct.pnl == pytest.approx(40.0)
    assert ct.pnl_pct == pytest.approx(10.0)


def test_reduce_shares_to_zero():
    t = _make_tracker_with_long()
    t.reduce_shares("AAPL", 10, 105.0, 3000)
    assert "AAPL" not in t
    assert len(t) == 0


# --- Close position ---

def test_close_position():
    t = _make_tracker_with_long()
    pnl = t.close_position("AAPL", 120.0, 4000)
    assert pnl == pytest.approx(200.0)
    assert "AAPL" not in t
    assert t.realized_pnl == pytest.approx(200.0)


# --- Deployed capital ---

def test_deployed_capital():
    t = _make_tracker_with_long()
    t.open_position("MSFT", "LONG", 5, 200.0, 1001, 190.0, 200.0)
    assert t.deployed_capital == pytest.approx(2000.0)  # 10*100 + 5*200


# --- PnL aggregates ---

def test_pnl_aggregates():
    t = _make_tracker_with_long()
    t.update_price("AAPL", 110.0)  # +100 unrealized
    t.open_position("MSFT", "LONG", 5, 200.0, 1001, 190.0, 190.0)
    # MSFT unrealized = -50
    t.reduce_shares("AAPL", 5, 110.0, 3000)  # realized +50
    # remaining AAPL: 5 shares, unrealized still (110-100)*5=50
    assert t.realized_pnl == pytest.approx(50.0)
    assert t.unrealized_pnl == pytest.approx(0.0)  # 50 + (-50)
    assert t.total_pnl == pytest.approx(50.0)


# --- as_share_counts ---

def test_as_share_counts():
    t = _make_tracker_with_long()
    t.open_position("MSFT", "LONG", 5, 200.0, 1001, 190.0, 200.0)
    counts = t.as_share_counts()
    assert counts == {"AAPL": 10, "MSFT": 5}


# --- Edge cases ---

def test_get_unknown():
    t = PositionTracker()
    assert t.get("NOPE") is None


def test_update_price_unknown_raises():
    t = PositionTracker()
    with pytest.raises(KeyError):
        t.update_price("NOPE", 10.0)


def test_contains_and_len():
    t = PositionTracker()
    assert len(t) == 0
    assert "AAPL" not in t
    t.open_position("AAPL", "LONG", 1, 100.0, 1000, 95.0, 100.0)
    assert len(t) == 1
    assert "AAPL" in t


def test_positions_returns_copy():
    t = _make_tracker_with_long()
    copy = t.positions
    del copy["AAPL"]
    assert "AAPL" in t  # original unaffected
