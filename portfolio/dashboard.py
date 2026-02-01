"""FastAPI dashboard — REST endpoints for health, positions, P&L, risk, trades."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import aiosqlite
import uvicorn
from fastapi import FastAPI, Query

from data.store import query_trades
from portfolio.analytics import compute_metrics, compute_metrics_by_group


def create_app(
    tracker,
    risk_controller,
    reconciler,
    db_path: str | Path,
    equity: float = 10_000.0,
) -> FastAPI:
    """Build a FastAPI app wired to live runtime objects.

    Parameters
    ----------
    tracker : PositionTracker
    risk_controller : RiskController
    reconciler : Reconciler
    db_path : path to SQLite database
    equity : starting equity for metric calculations
    """
    app = FastAPI(title="Agentic Investment Dashboard", version="0.1.0")
    app.state.tracker = tracker
    app.state.risk = risk_controller
    app.state.reconciler = reconciler
    app.state.db_path = str(db_path)
    app.state.equity = equity

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_db() -> aiosqlite.Connection:
        db = await aiosqlite.connect(app.state.db_path)
        db.row_factory = aiosqlite.Row
        return db

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
        db = await _get_db()
        try:
            trades = await query_trades(db)
            return compute_metrics(trades, app.state.equity)
        finally:
            await db.close()

    @app.get("/api/metrics/sectors")
    async def metrics_sectors():
        db = await _get_db()
        try:
            trades = await query_trades(db)
            return compute_metrics_by_group(trades, app.state.equity, "sector")
        finally:
            await db.close()

    @app.get("/api/risk")
    async def risk():
        # Return latest evaluate() snapshot if available; otherwise basic status
        return {
            "halted": app.state.risk.is_halted,
        }

    @app.get("/api/trades")
    async def trades(
        limit: int = Query(50, ge=1, le=1000),
        symbol: str | None = Query(None),
        sector: str | None = Query(None),
    ):
        db = await _get_db()
        try:
            return await query_trades(
                db, symbol=symbol, sector=sector, limit=limit,
            )
        finally:
            await db.close()

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
