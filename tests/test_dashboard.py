"""Tests for portfolio/dashboard.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from data.models import Position, ReconciliationSnapshot, RiskStatus
from portfolio.dashboard import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_position(**overrides) -> Position:
    defaults = dict(
        symbol="AAPL",
        direction="LONG",
        shares=10,
        entry_price=150.0,
        entry_time=1000000,
        current_price=155.0,
        stop_price=145.0,
        unrealized_pnl=50.0,
        sector="tech",
    )
    defaults.update(overrides)
    return Position(**defaults)


def _make_tracker(positions: dict[str, Position] | None = None) -> MagicMock:
    tracker = MagicMock()
    pos = positions or {}
    tracker.positions = pos
    tracker.__len__ = lambda self: len(pos)
    tracker.realized_pnl = 100.0
    tracker.unrealized_pnl = 50.0
    tracker.total_pnl = 150.0
    tracker.deployed_capital = 3000.0
    return tracker


def _make_risk(halted: bool = False) -> MagicMock:
    rc = MagicMock()
    rc.is_halted = halted
    return rc


def _make_reconciler(halted: bool = False, history: list | None = None) -> MagicMock:
    rec = MagicMock()
    rec.is_halted = halted
    rec.history = history or []
    return rec


@pytest.fixture
def client():
    """TestClient with default mocks (no positions, not halted)."""
    app = create_app(
        tracker=_make_tracker(),
        risk_controller=_make_risk(),
        reconciler=_make_reconciler(),
        db_path=":memory:",
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["halted"] is False
        assert data["positions_count"] == 0
        assert "timestamp" in data

    def test_health_halted_risk(self):
        app = create_app(
            tracker=_make_tracker(),
            risk_controller=_make_risk(halted=True),
            reconciler=_make_reconciler(),
            db_path=":memory:",
        )
        resp = TestClient(app).get("/api/health")
        assert resp.json()["halted"] is True

    def test_health_halted_reconciler(self):
        app = create_app(
            tracker=_make_tracker(),
            risk_controller=_make_risk(),
            reconciler=_make_reconciler(halted=True),
            db_path=":memory:",
        )
        resp = TestClient(app).get("/api/health")
        assert resp.json()["halted"] is True


class TestPositions:
    def test_positions_empty(self, client):
        resp = client.get("/api/positions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_positions_with_data(self):
        pos = _make_position()
        app = create_app(
            tracker=_make_tracker({"AAPL": pos}),
            risk_controller=_make_risk(),
            reconciler=_make_reconciler(),
            db_path=":memory:",
        )
        resp = TestClient(app).get("/api/positions")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "AAPL"
        assert data[0]["unrealized_pnl"] == 50.0


class TestPnl:
    def test_pnl(self, client):
        resp = client.get("/api/pnl")
        assert resp.status_code == 200
        data = resp.json()
        assert data["realized"] == 100.0
        assert data["unrealized"] == 50.0
        assert data["total"] == 150.0
        assert data["deployed_capital"] == 3000.0


class TestMetrics:
    @patch("portfolio.dashboard.query_trades", new_callable=AsyncMock)
    @patch("portfolio.dashboard.compute_metrics")
    def test_metrics(self, mock_compute, mock_qt):
        mock_qt.return_value = [{"pnl": 10.0, "exit_time": 100}]
        mock_compute.return_value = {"sharpe_ratio": 1.5, "win_rate": 0.6}
        app = create_app(
            tracker=_make_tracker(),
            risk_controller=_make_risk(),
            reconciler=_make_reconciler(),
            db_path=":memory:",
        )
        resp = TestClient(app).get("/api/metrics")
        assert resp.status_code == 200
        assert resp.json()["sharpe_ratio"] == 1.5
        mock_compute.assert_called_once()

    @patch("portfolio.dashboard.query_trades", new_callable=AsyncMock)
    @patch("portfolio.dashboard.compute_metrics_by_group")
    def test_metrics_sectors(self, mock_group, mock_qt):
        mock_qt.return_value = [{"pnl": 10.0, "exit_time": 100, "sector": "tech"}]
        mock_group.return_value = {"tech": {"win_rate": 0.7}}
        app = create_app(
            tracker=_make_tracker(),
            risk_controller=_make_risk(),
            reconciler=_make_reconciler(),
            db_path=":memory:",
        )
        resp = TestClient(app).get("/api/metrics/sectors")
        assert resp.status_code == 200
        assert "tech" in resp.json()


class TestRisk:
    def test_risk(self, client):
        resp = client.get("/api/risk")
        assert resp.status_code == 200
        assert resp.json()["halted"] is False


class TestTrades:
    @patch("portfolio.dashboard.query_trades", new_callable=AsyncMock)
    def test_trades_default(self, mock_qt):
        mock_qt.return_value = [{"symbol": "AAPL", "pnl": 25.0}]
        app = create_app(
            tracker=_make_tracker(),
            risk_controller=_make_risk(),
            reconciler=_make_reconciler(),
            db_path=":memory:",
        )
        resp = TestClient(app).get("/api/trades")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        # Verify limit default
        mock_qt.assert_called_once()
        _, kwargs = mock_qt.call_args
        assert kwargs["limit"] == 50

    @patch("portfolio.dashboard.query_trades", new_callable=AsyncMock)
    def test_trades_with_params(self, mock_qt):
        mock_qt.return_value = []
        app = create_app(
            tracker=_make_tracker(),
            risk_controller=_make_risk(),
            reconciler=_make_reconciler(),
            db_path=":memory:",
        )
        resp = TestClient(app).get("/api/trades?limit=10&symbol=TSLA&sector=auto")
        assert resp.status_code == 200
        _, kwargs = mock_qt.call_args
        assert kwargs["limit"] == 10
        assert kwargs["symbol"] == "TSLA"
        assert kwargs["sector"] == "auto"


class TestReconciliation:
    def test_reconciliation_empty(self, client):
        resp = client.get("/api/reconciliation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["latest"] is None
        assert data["history"] == []

    def test_reconciliation_with_history(self):
        snaps = [
            ReconciliationSnapshot(
                timestamp=1000 + i,
                matches=True,
                mismatches=[],
                bot_total_symbols=2,
                ibkr_total_symbols=5,
                protected_total_symbols=3,
            )
            for i in range(3)
        ]
        app = create_app(
            tracker=_make_tracker(),
            risk_controller=_make_risk(),
            reconciler=_make_reconciler(history=snaps),
            db_path=":memory:",
        )
        resp = TestClient(app).get("/api/reconciliation")
        data = resp.json()
        assert data["latest"]["timestamp"] == 1002
        assert len(data["history"]) == 3
