"""Tests for portfolio/reports.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from data.models import Position, ReconciliationSnapshot
from portfolio.dashboard import create_app
from portfolio.reports import (
    build_daily_report,
    build_weekly_report,
    generate_daily_report,
    generate_weekly_report,
    send_daily_report,
)


# ── Helpers ──────────────────────────────────────────────────────


def _make_tracker(positions=None, realized=100.0, unrealized=50.0, deployed=3000.0):
    t = MagicMock()
    pos = positions or {}
    t.positions = pos
    t.__len__ = lambda self: len(pos)
    t.realized_pnl = realized
    t.unrealized_pnl = unrealized
    t.total_pnl = realized + unrealized
    t.deployed_capital = deployed
    return t


def _make_risk(halted=False):
    r = MagicMock()
    r.is_halted = halted
    return r


def _make_reconciler(halted=False, history=None):
    r = MagicMock()
    r.is_halted = halted
    r.history = history or []
    return r


def _sample_trades():
    return [
        {"symbol": "NVDA", "pnl": 65.0, "exit_time": 1000, "sector": "tech"},
        {"symbol": "AAPL", "pnl": 30.0, "exit_time": 1001, "sector": "tech"},
        {"symbol": "TSLA", "pnl": 20.0, "exit_time": 1002, "sector": "auto"},
        {"symbol": "AMD", "pnl": -22.0, "exit_time": 1003, "sector": "tech"},
    ]


def _sample_metrics():
    return {
        "count": 4,
        "total_pnl": 93.0,
        "win_count": 3,
        "loss_count": 1,
        "win_rate": 0.75,
        "profit_factor": 2.8,
        "sharpe_ratio": 1.85,
        "max_drawdown_pct": 1.2,
        "best_trade_pnl": 65.0,
        "worst_trade_pnl": -22.0,
    }


def _pos(symbol, direction="LONG", shares=10, entry=150.0, current=155.0, upnl=50.0):
    return Position(
        symbol=symbol,
        direction=direction,
        shares=shares,
        entry_price=entry,
        entry_time=1000000,
        current_price=current,
        stop_price=145.0,
        unrealized_pnl=upnl,
        sector="tech",
    )


# ── build_daily_report ───────────────────────────────────────────


class TestBuildDailyReport:
    def test_basic(self):
        trades = _sample_trades()
        metrics = _sample_metrics()
        tracker = _make_tracker({"AAPL": _pos("AAPL")})
        report = build_daily_report(trades, metrics, tracker, False, True)
        assert "DAILY REPORT" in report
        assert "3W / 1L" in report
        assert "75.0%" in report
        assert "NVDA" in report
        assert "AMD" in report
        assert "AAPL LONG 10" in report
        assert "Risk: OK" in report
        assert "matched" in report

    def test_zero_trades(self):
        metrics = {"count": 0, "win_count": 0, "loss_count": 0, "win_rate": 0.0, "profit_factor": 0.0}
        tracker = _make_tracker(realized=0.0, unrealized=0.0, deployed=0.0)
        report = build_daily_report([], metrics, tracker, False, True)
        assert "Trades: 0" in report
        assert "0W / 0L" in report

    def test_halted(self):
        metrics = _sample_metrics()
        tracker = _make_tracker()
        report = build_daily_report([], metrics, tracker, True, True)
        assert "Risk: HALTED" in report

    def test_recon_mismatch(self):
        metrics = _sample_metrics()
        tracker = _make_tracker()
        report = build_daily_report([], metrics, tracker, False, False)
        assert "MISMATCH" in report


# ── build_weekly_report ──────────────────────────────────────────


class TestBuildWeeklyReport:
    def test_with_sectors(self):
        trades = _sample_trades()
        metrics = _sample_metrics()
        sector_metrics = {
            "tech": {"total_pnl": 73.0, "count": 3, "win_rate": 0.667},
            "auto": {"total_pnl": 20.0, "count": 1, "win_rate": 1.0},
        }
        snaps = [MagicMock(matches=True) for _ in range(5)]
        tracker = _make_tracker()
        report = build_weekly_report(trades, metrics, sector_metrics, tracker, snaps)
        assert "WEEKLY REPORT" in report
        assert "tech:" in report
        assert "auto:" in report
        assert "5/5 days matched" in report
        assert "Sharpe: 1.85" in report

    def test_zero_trades(self):
        metrics = {"count": 0, "win_count": 0, "loss_count": 0, "win_rate": 0.0,
                   "profit_factor": 0.0, "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0,
                   "total_pnl": 0.0}
        tracker = _make_tracker(realized=0.0, unrealized=0.0)
        report = build_weekly_report([], metrics, {}, tracker, [])
        assert "Total trades: 0" in report
        assert "(none)" in report


# ── Async orchestrators ──────────────────────────────────────────


class TestGenerateDaily:
    @patch("portfolio.reports.aiosqlite")
    @patch("portfolio.reports.query_trades", new_callable=AsyncMock)
    @patch("portfolio.reports.compute_metrics")
    async def test_generate(self, mock_cm, mock_qt, mock_aio):
        mock_db = AsyncMock()
        mock_aio.connect = AsyncMock(return_value=mock_db)
        mock_aio.Row = None
        mock_qt.return_value = _sample_trades()
        mock_cm.return_value = _sample_metrics()
        snap = MagicMock(matches=True)
        tracker = _make_tracker()
        risk = _make_risk()
        recon = _make_reconciler(history=[snap])

        report = await generate_daily_report(tracker, risk, recon, ":memory:")
        assert "DAILY REPORT" in report
        mock_qt.assert_called_once()


class TestGenerateWeekly:
    @patch("portfolio.reports.aiosqlite")
    @patch("portfolio.reports.query_trades", new_callable=AsyncMock)
    @patch("portfolio.reports.compute_metrics")
    @patch("portfolio.reports.compute_metrics_by_group")
    async def test_generate(self, mock_sector, mock_cm, mock_qt, mock_aio):
        mock_db = AsyncMock()
        mock_aio.connect = AsyncMock(return_value=mock_db)
        mock_aio.Row = None
        mock_qt.return_value = _sample_trades()
        mock_cm.return_value = _sample_metrics()
        mock_sector.return_value = {"tech": {"total_pnl": 73.0, "count": 3, "win_rate": 0.667}}
        tracker = _make_tracker()
        risk = _make_risk()
        recon = _make_reconciler(history=[])

        report = await generate_weekly_report(tracker, risk, recon, ":memory:")
        assert "WEEKLY REPORT" in report


class TestSendDaily:
    @patch("portfolio.reports.send_whatsapp", new_callable=AsyncMock)
    @patch("portfolio.reports.aiosqlite")
    @patch("portfolio.reports.query_trades", new_callable=AsyncMock)
    @patch("portfolio.reports.compute_metrics")
    async def test_send(self, mock_cm, mock_qt, mock_aio, mock_wa):
        mock_db = AsyncMock()
        mock_aio.connect = AsyncMock(return_value=mock_db)
        mock_aio.Row = None
        mock_qt.return_value = []
        mock_cm.return_value = _sample_metrics()
        mock_wa.return_value = True
        tracker = _make_tracker()
        risk = _make_risk()
        recon = _make_reconciler()

        report = await send_daily_report(tracker, risk, recon, ":memory:")
        assert "DAILY REPORT" in report
        mock_wa.assert_called_once()


# ── Dashboard endpoints ──────────────────────────────────────────


class TestDashboardReports:
    @patch("portfolio.reports.aiosqlite")
    @patch("portfolio.reports.query_trades", new_callable=AsyncMock)
    @patch("portfolio.reports.compute_metrics")
    def test_daily_endpoint(self, mock_cm, mock_qt, mock_aio):
        mock_db = AsyncMock()
        mock_aio.connect = AsyncMock(return_value=mock_db)
        mock_aio.Row = None
        mock_qt.return_value = []
        mock_cm.return_value = _sample_metrics()
        app = create_app(
            tracker=_make_tracker(),
            risk_controller=_make_risk(),
            reconciler=_make_reconciler(),
            db_path=":memory:",
        )
        resp = TestClient(app).get("/api/reports/daily")
        assert resp.status_code == 200
        data = resp.json()
        assert "report" in data
        assert "DAILY REPORT" in data["report"]
        assert "timestamp" in data

    @patch("portfolio.reports.aiosqlite")
    @patch("portfolio.reports.query_trades", new_callable=AsyncMock)
    @patch("portfolio.reports.compute_metrics")
    @patch("portfolio.reports.compute_metrics_by_group")
    def test_weekly_endpoint(self, mock_sector, mock_cm, mock_qt, mock_aio):
        mock_db = AsyncMock()
        mock_aio.connect = AsyncMock(return_value=mock_db)
        mock_aio.Row = None
        mock_qt.return_value = []
        mock_cm.return_value = _sample_metrics()
        mock_sector.return_value = {}
        app = create_app(
            tracker=_make_tracker(),
            risk_controller=_make_risk(),
            reconciler=_make_reconciler(),
            db_path=":memory:",
        )
        resp = TestClient(app).get("/api/reports/weekly")
        assert resp.status_code == 200
        data = resp.json()
        assert "report" in data
        assert "WEEKLY REPORT" in data["report"]
