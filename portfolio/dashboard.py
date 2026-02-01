"""FastAPI dashboard — REST endpoints for health, positions, P&L, risk, trades."""

from __future__ import annotations

import threading
import time

import uvicorn
from fastapi import FastAPI, Query

from data.store import get_db, query_trades
from portfolio.analytics import compute_metrics, compute_metrics_by_group
from portfolio.reports import generate_daily_report, generate_weekly_report


def create_app(
    tracker,
    risk_controller,
    reconciler,
    equity: float = 10_000.0,
) -> FastAPI:
    """Build a FastAPI app wired to live runtime objects.

    Parameters
    ----------
    tracker : PositionTracker
    risk_controller : RiskController
    reconciler : Reconciler
    equity : starting equity for metric calculations
    """
    app = FastAPI(title="Agentic Investment Dashboard", version="0.1.0")
    app.state.tracker = tracker
    app.state.risk = risk_controller
    app.state.reconciler = reconciler
    app.state.equity = equity

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @app.get("/api/health")
    async def health():
        halted = app.state.risk.is_halted or app.state.reconciler.is_halted
        return {
            "status": "ok",
            "timestamp": int(time.time()),
            "halted": halted,
            "positions_count": len(app.state.tracker),
        }

    @app.get("/api/positions")
    async def positions():
        pos_dict = app.state.tracker.positions
        return [p.model_dump() for p in pos_dict.values()]

    @app.get("/api/pnl")
    async def pnl():
        t = app.state.tracker
        return {
            "realized": t.realized_pnl,
            "unrealized": t.unrealized_pnl,
            "total": t.total_pnl,
            "deployed_capital": t.deployed_capital,
        }

    @app.get("/api/metrics")
    async def metrics():
        async with get_db() as conn:
            trades = await query_trades(conn)
        return compute_metrics(trades, app.state.equity)

    @app.get("/api/metrics/sectors")
    async def metrics_sectors():
        async with get_db() as conn:
            trades = await query_trades(conn)
        return compute_metrics_by_group(trades, app.state.equity, "sector")

    @app.get("/api/risk")
    async def risk():
        return {
            "halted": app.state.risk.is_halted,
        }

    @app.get("/api/trades")
    async def trades(
        limit: int = Query(50, ge=1, le=1000),
        symbol: str | None = Query(None),
        sector: str | None = Query(None),
    ):
        async with get_db() as conn:
            return await query_trades(
                conn, symbol=symbol, sector=sector, limit=limit,
            )

    @app.get("/api/reports/daily")
    async def report_daily():
        report = await generate_daily_report(
            app.state.tracker,
            app.state.risk,
            app.state.reconciler,
            app.state.equity,
        )
        return {"report": report, "timestamp": int(time.time())}

    @app.get("/api/reports/weekly")
    async def report_weekly():
        report = await generate_weekly_report(
            app.state.tracker,
            app.state.risk,
            app.state.reconciler,
            app.state.equity,
        )
        return {"report": report, "timestamp": int(time.time())}

    @app.get("/api/reconciliation")
    async def reconciliation():
        history = app.state.reconciler.history
        latest = history[-1].model_dump() if history else None
        recent = [s.model_dump() for s in history[-10:]]
        return {
            "latest": latest,
            "history": recent,
        }

    return app


def start_dashboard(
    app: FastAPI,
    host: str = "0.0.0.0",
    port: int = 8000,
) -> uvicorn.Server:
    """Start uvicorn in a daemon thread (non-blocking)."""
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server
