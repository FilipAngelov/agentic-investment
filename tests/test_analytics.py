"""Tests for portfolio.analytics."""

import math

import pytest

from portfolio.analytics import (
    _daily_pnl_series,
    _max_drawdown,
    _sharpe_ratio,
    _sortino_ratio,
    compute_metrics,
    compute_metrics_by_group,
)

EQUITY = 10_000.0

# Helper: build a trade dict
def _trade(pnl: float, exit_time: int, **kw) -> dict:
    base = {
        "symbol": "AAPL",
        "direction": "long",
        "entry_time": exit_time - 3600,
        "entry_price": 100.0,
        "entry_shares": 10,
        "exit_time": exit_time,
        "exit_price": 100.0 + pnl / 10,
        "exit_shares": 10,
        "pnl": pnl,
        "pnl_pct": pnl / 1000.0,
        "sector": "tech",
        "regime": "bull",
        "exit_reason": "target",
    }
    base.update(kw)
    return base


# Day timestamps (UTC) — use noon to avoid DST issues
DAY1 = 1700049600  # 2023-11-15 12:00 UTC
DAY2 = DAY1 + 86400
DAY3 = DAY2 + 86400
DAY4 = DAY3 + 86400
DAY5 = DAY4 + 86400


class TestEmptyTrades:
    def test_empty_list(self):
        m = compute_metrics([], EQUITY)
        assert m["count"] == 0
        assert m["total_pnl"] == 0.0
        assert m["sharpe_ratio"] == 0.0
        assert m["daily_returns"] == []

    def test_zero_equity(self):
        trades = [_trade(50, DAY1)]
        m = compute_metrics(trades, 0)
        assert m["count"] == 0


class TestSingleWin:
    def test_single_winning_trade(self):
        trades = [_trade(100, DAY1)]
        m = compute_metrics(trades, EQUITY)
        assert m["count"] == 1
        assert m["win_count"] == 1
        assert m["loss_count"] == 0
        assert m["win_rate"] == 1.0
        assert m["total_pnl"] == 100.0
        assert m["avg_win"] == 100.0
        assert m["best_trade_pnl"] == 100.0
        assert m["worst_trade_pnl"] == 100.0
        assert m["total_return_pct"] == pytest.approx(1.0)


class TestMixedTrades:
    @pytest.fixture()
    def metrics(self):
        trades = [
            _trade(200, DAY1),
            _trade(-50, DAY2),
            _trade(100, DAY3),
            _trade(-30, DAY4),
            _trade(150, DAY5),
        ]
        return compute_metrics(trades, EQUITY)

    def test_counts(self, metrics):
        assert metrics["count"] == 5
        assert metrics["win_count"] == 3
        assert metrics["loss_count"] == 2

    def test_win_rate(self, metrics):
        assert metrics["win_rate"] == pytest.approx(0.6)

    def test_profit_factor(self, metrics):
        # gross_profit=450, gross_loss=80
        assert metrics["profit_factor"] == pytest.approx(450 / 80)

    def test_avg_win_loss_ratio(self, metrics):
        avg_w = 450 / 3
        avg_l = 80 / 2
        assert metrics["avg_win_loss_ratio"] == pytest.approx(avg_w / avg_l)

    def test_total_pnl(self, metrics):
        assert metrics["total_pnl"] == pytest.approx(370)

    def test_best_worst(self, metrics):
        assert metrics["best_trade_pnl"] == 200
        assert metrics["worst_trade_pnl"] == -50


class TestMaxConsecutiveLosses:
    def test_streak(self):
        trades = [
            _trade(100, DAY1),
            _trade(-10, DAY1 + 1),
            _trade(-20, DAY1 + 2),
            _trade(-5, DAY1 + 3),
            _trade(50, DAY1 + 4),
            _trade(-10, DAY1 + 5),
        ]
        m = compute_metrics(trades, EQUITY)
        assert m["max_consecutive_losses"] == 3


class TestMaxDrawdown:
    def test_drawdown(self):
        # Day1: +200, Day2: -300, Day3: +50
        # Cumulative: 200, -100, -50
        # Peak: 200, DD from peak: 0, 300, 250 → max DD = 300
        trades = [
            _trade(200, DAY1),
            _trade(-300, DAY2),
            _trade(50, DAY3),
        ]
        m = compute_metrics(trades, EQUITY)
        assert m["max_drawdown_pct"] == pytest.approx(3.0)  # 300/10000*100


class TestSharpe:
    def test_known_values(self):
        # 5 days of returns
        trades = [
            _trade(100, DAY1),
            _trade(50, DAY2),
            _trade(-20, DAY3),
            _trade(80, DAY4),
            _trade(30, DAY5),
        ]
        m = compute_metrics(trades, EQUITY)
        daily_r = [100/EQUITY, 50/EQUITY, -20/EQUITY, 80/EQUITY, 30/EQUITY]
        mu = sum(daily_r) / len(daily_r)
        std = math.sqrt(sum((x - mu)**2 for x in daily_r) / (len(daily_r) - 1))
        expected = (mu / std) * math.sqrt(252)
        assert m["sharpe_ratio"] == pytest.approx(expected, rel=1e-6)


class TestSortino:
    def test_only_downside(self):
        trades = [
            _trade(100, DAY1),
            _trade(50, DAY2),
            _trade(-20, DAY3),
            _trade(80, DAY4),
            _trade(-10, DAY5),
        ]
        m = compute_metrics(trades, EQUITY)
        daily_r = [100/EQUITY, 50/EQUITY, -20/EQUITY, 80/EQUITY, -10/EQUITY]
        mu = sum(daily_r) / len(daily_r)
        down = [r for r in daily_r if r < 0]
        down_std = math.sqrt(sum(r**2 for r in down) / len(down))
        expected = (mu / down_std) * math.sqrt(252)
        assert m["sortino_ratio"] == pytest.approx(expected, rel=1e-6)


class TestVaR95:
    def test_var(self):
        # 20 trades on 20 different days
        trades = [_trade((i - 10) * 10, DAY1 + i * 86400) for i in range(20)]
        m = compute_metrics(trades, EQUITY)
        daily_r = sorted([(i - 10) * 10 / EQUITY for i in range(20)])
        idx = int(20 * 0.05)  # = 1
        expected = abs(daily_r[idx]) * 100
        assert m["var_95_pct"] == pytest.approx(expected)


class TestCalmar:
    def test_calmar(self):
        trades = [
            _trade(200, DAY1),
            _trade(-300, DAY2),
            _trade(50, DAY3),
        ]
        m = compute_metrics(trades, EQUITY)
        # total_return_pct = -50/10000*100 = -0.5
        # ann_return = -0.5/3*252
        # max_dd = 3.0
        ann = (-0.5 / 3) * 252
        expected = ann / 3.0
        assert m["calmar_ratio"] == pytest.approx(expected)


class TestTradesPerDay:
    def test_trades_per_day(self):
        # 4 trades across 2 days
        trades = [
            _trade(10, DAY1),
            _trade(20, DAY1 + 1),
            _trade(-5, DAY2),
            _trade(15, DAY2 + 1),
        ]
        m = compute_metrics(trades, EQUITY)
        assert m["trades_per_day"] == pytest.approx(2.0)


class TestMaxSingleDayLoss:
    def test_worst_day(self):
        trades = [
            _trade(100, DAY1),
            _trade(-200, DAY2),
            _trade(-50, DAY3),
        ]
        m = compute_metrics(trades, EQUITY)
        assert m["max_single_day_loss_pct"] == pytest.approx(2.0)  # 200/10000*100


class TestGroupBy:
    def test_by_sector(self):
        trades = [
            _trade(100, DAY1, sector="tech"),
            _trade(-50, DAY2, sector="health"),
            _trade(80, DAY3, sector="tech"),
        ]
        groups = compute_metrics_by_group(trades, EQUITY, "sector")
        assert set(groups.keys()) == {"tech", "health"}
        assert groups["tech"]["count"] == 2
        assert groups["health"]["count"] == 1

    def test_by_regime(self):
        trades = [
            _trade(100, DAY1, regime="bull"),
            _trade(-50, DAY2, regime="bear"),
        ]
        groups = compute_metrics_by_group(trades, EQUITY, "regime")
        assert "bull" in groups
        assert "bear" in groups


class TestDailyPnlSeries:
    def test_same_day_aggregation(self):
        trades = [
            _trade(100, DAY1),
            _trade(-30, DAY1 + 60),
        ]
        series = _daily_pnl_series(trades)
        assert len(series) == 1
        assert series[0][1] == pytest.approx(70)

    def test_multi_day(self):
        trades = [
            _trade(100, DAY1),
            _trade(-30, DAY2),
            _trade(50, DAY3),
        ]
        series = _daily_pnl_series(trades)
        assert len(series) == 3
