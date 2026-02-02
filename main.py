"""Orchestrator: connect IB, init components, run trading loops."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from typing import Any

import asyncpg
from ib_async import IB, Stock

from config.logging import setup_logging
from config.sectors import (
    BENCHMARK_SPY,
    BENCHMARK_VIX,
    SECTOR_ETF_SYMBOLS,
)
from config.settings import (
    DATABASE_URL,
    DRY_RUN,
    ib_config,
    load_protected_positions,
    risk_config,
)
from data.ingest import sync_symbol, sync_universe
from data.indicators import atr as compute_atr
from data.models import AccountState, Bar, Catalyst, Position
from data.store import close_pool, get_db, init_db, query_bars, query_catalysts
from execution.orders import OrderManager
from execution.partial_exit import PartialExitManager
from execution.reconciliation import Reconciler
from execution.risk import RiskController
from execution.time_decay import TimeDecayManager
from execution.trailing_stop import TrailingStopManager
from portfolio.dashboard import create_app, start_dashboard
from portfolio.notify import (
    notify_entry,
    notify_exit,
    notify_reconciliation_fail,
    notify_risk,
)
from portfolio.reports import send_daily_report
from portfolio.trade_logger import TradeLogger
from portfolio.tracker import PositionTracker
from scanner.candidate_ranker import CandidateRanker
from scanner.news import CatalystEngine
from scanner.regime import RegimeDetector
from scanner.screener import MarketScanner
from scanner.sectors import SectorTracker
from signals.pipeline import generate_signal

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Eastern time helpers
# ---------------------------------------------------------------------------

_ET = ZoneInfo("America/New_York")


def _now_et() -> datetime:
    return datetime.now(_ET)


def is_market_hours() -> bool:
    """True during regular market hours (9:30–16:00 ET, weekdays)."""
    now = _now_et()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dt_time(9, 30) <= t < dt_time(16, 0)


def is_premarket() -> bool:
    """True during pre-market window (9:00–9:30 ET, weekdays)."""
    now = _now_et()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dt_time(9, 0) <= t < dt_time(9, 30)


def _is_after(hour: int, minute: int) -> bool:
    t = _now_et().time()
    return t >= dt_time(hour, minute)


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------


@dataclass
class SharedState:
    ib: IB
    pool: asyncpg.Pool
    sector_tracker: SectorTracker
    regime_detector: RegimeDetector
    scanner: MarketScanner
    ranker: CandidateRanker
    catalyst_engine: CatalystEngine
    order_manager: OrderManager
    tracker: PositionTracker
    risk_controller: RiskController
    reconciler: Reconciler
    trade_logger: TradeLogger
    trailing_stop: TrailingStopManager
    partial_exit: PartialExitManager
    time_decay: TimeDecayManager
    protected: dict[str, int]
    warmup_done: bool = False
    shutdown: bool = False
    # streaming tickers keyed by symbol
    _streaming: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# IB connection with retry
# ---------------------------------------------------------------------------


async def connect_ib() -> IB:
    """Connect to IB Gateway, retrying every 30s."""
    ib = IB()
    cfg = ib_config
    while True:
        try:
            await ib.connectAsync(cfg.host, cfg.port, clientId=cfg.client_id)
            ib.RaiseRequestErrors = True

            def _on_ib_error(reqId, errorCode, errorString, contract):
                if errorCode in (162, 165, 2103, 2104, 2105, 2106):
                    log.debug("IBKR info %d (reqId=%d): %s", errorCode, reqId, errorString)
                else:
                    log.warning("IBKR error %d (reqId=%d): %s %s", errorCode, reqId, errorString, contract or "")

            ib.errorEvent += _on_ib_error
            log.info("Connected to IB Gateway at %s:%s", cfg.host, cfg.port)
            if DRY_RUN:
                log.info("DRY_RUN mode — orders will NOT be placed")
            return ib
        except Exception:
            log.warning("IB connection failed, retrying in 30s…", exc_info=True)
            await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# Account state builder
# ---------------------------------------------------------------------------


async def build_account_state(ib: IB, tracker: PositionTracker) -> AccountState:
    """Parse IBKR accountSummary into AccountState."""
    values = await ib.accountSummaryAsync()
    d: dict[str, str] = {}
    for av in values:
        d[av.tag] = av.value

    return AccountState(
        net_liquidation=float(d.get("NetLiquidation", 0)),
        total_cash_value=float(d.get("TotalCashValue", 0)),
        buying_power=float(d.get("BuyingPower", 0)),
        available_funds=float(d.get("AvailableFunds", 0)),
        excess_liquidity=float(d.get("ExcessLiquidity", 0)),
        init_margin_req=float(d.get("InitMarginReq", 0)),
        maint_margin_req=float(d.get("MaintMarginReq", 0)),
        sma=float(d.get("SMA", 0)),
        day_trades_remaining=int(d.get("DayTradesRemaining", -1)),
        daily_pnl=float(d.get("RealizedPnL", 0)),
    )


# ---------------------------------------------------------------------------
# Bootstrap data
# ---------------------------------------------------------------------------


async def bootstrap_data(state: SharedState) -> None:
    """Fetch initial historical data into PostgreSQL."""
    log.info("Data bootstrap starting…")
    universe = SECTOR_ETF_SYMBOLS + [BENCHMARK_SPY, BENCHMARK_VIX]
    async with state.pool.acquire() as conn:
        # Daily bars — 500 days
        await sync_universe(state.ib, conn, universe, ["1d"])
        # 5-min bars — sector ETFs only, 5 days
        for sym in SECTOR_ETF_SYMBOLS:
            await sync_symbol(state.ib, conn, sym, "5m", 5)
    log.info("Data bootstrap complete")


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------


def subscribe_streaming(state: SharedState, symbol: str) -> None:
    """Subscribe to market data for a position."""
    if symbol in state._streaming:
        return
    contract = Stock(symbol, "SMART", "USD")
    state.ib.reqMktData(contract, genericTickList="", snapshot=False)
    state._streaming[symbol] = contract
    log.debug("Subscribed streaming for %s", symbol)


def unsubscribe_streaming(state: SharedState, symbol: str) -> None:
    """Unsubscribe market data for a closed position."""
    contract = state._streaming.pop(symbol, None)
    if contract is not None:
        state.ib.cancelMktData(contract)
        log.debug("Unsubscribed streaming for %s", symbol)


def get_streaming_price(state: SharedState, symbol: str) -> float | None:
    """Read latest price from streaming ticker."""
    contract = state._streaming.get(symbol)
    if contract is None:
        return None
    ticker = state.ib.ticker(contract)
    if ticker and ticker.last and ticker.last > 0:
        return ticker.last
    if ticker and ticker.close and ticker.close > 0:
        return ticker.close
    return None


# ---------------------------------------------------------------------------
# Close and log helper
# ---------------------------------------------------------------------------


async def close_and_log(
    state: SharedState,
    symbol: str,
    exit_price: float,
    exit_reason: str,
) -> None:
    """Close a position, log the trade, notify, and unsubscribe streaming."""
    pos = state.tracker.get(symbol)
    if pos is None:
        return
    pnl = state.tracker.close_position(symbol, exit_price, int(time.time()))

    # Log trade
    closed_trades = state.tracker.closed_trades
    if closed_trades:
        latest = closed_trades[-1]
        async with state.pool.acquire() as conn:
            await state.trade_logger.record_exit(conn, latest, exit_reason)

    # Notify
    pnl_pct = (pnl / (pos.entry_price * pos.shares)) * 100 if pos.shares else 0
    await notify_exit(
        symbol=symbol,
        direction=pos.direction,
        shares=pos.shares,
        entry_price=pos.entry_price,
        exit_price=exit_price,
        pnl=pnl,
        pnl_pct=pnl_pct,
        exit_reason=exit_reason,
    )

    # Cleanup
    unsubscribe_streaming(state, symbol)
    state.partial_exit.reset(symbol)


# ---------------------------------------------------------------------------
# Process candidates (signal → execution)
# ---------------------------------------------------------------------------


async def process_candidates(state: SharedState, candidates: list[dict]) -> None:
    """For each candidate: fetch data, generate signal, execute if valid."""
    if state.risk_controller.is_halted or state.reconciler.is_halted:
        log.info("Trading halted — skipping signal generation")
        return
    if not state.warmup_done:
        log.info("Warmup period — skipping signal generation")
        return

    regime = state.regime_detector.get_current_regime()
    if regime is None:
        log.warning("No regime classified yet — skipping")
        return

    account = await build_account_state(state.ib, state.tracker)
    bot_positions = {s: p for s, p in state.tracker.positions.items()}
    since_ts = int(time.time()) - 48 * 3600

    for cand in candidates[:10]:
        symbol = cand.get("symbol")
        if not symbol or symbol in state.tracker:
            continue
        sector = cand.get("sector")
        direction_hint = cand.get("direction")

        try:
            # Ensure bars exist
            async with state.pool.acquire() as conn:
                existing_bars = await query_bars(conn, symbol=symbol, timeframe="1d")
                if not existing_bars:
                    synced = await sync_symbol(state.ib, conn, symbol, "1d", 65)
                    log.info("Synced %d daily bars for candidate %s", synced, symbol)

                # Fetch bars from DB
                stock_rows = await query_bars(conn, symbol=symbol, timeframe="1d")
                spy_rows = await query_bars(conn, symbol=BENCHMARK_SPY, timeframe="1d")
                catalyst_rows = await query_catalysts(conn, since_ts=since_ts, symbol=symbol)

            stock_bars = [Bar(**r) for r in stock_rows] if stock_rows else []
            benchmark_bars = [Bar(**r) for r in spy_rows] if spy_rows else []
            catalysts = [Catalyst(**r) for r in catalyst_rows] if catalyst_rows else []

            # Build sector tracker info
            sector_info = None
            if sector:
                from config.sectors import SECTOR_ETFS
                etf = SECTOR_ETFS.get(sector)
                if etf:
                    mom = state.sector_tracker.compute_momentum(etf)
                    rs = state.sector_tracker.compute_relative_strength(etf)
                    sector_info = {
                        "momentum": mom or 0.0,
                        "relative_strength": rs or 0.0,
                        "accelerating": False,
                    }

            # Build bars as dicts for pipeline
            bar_dicts = [b.model_dump() for b in stock_bars] if stock_bars else []

            sig = generate_signal(
                bars=bar_dicts,
                regime=regime,
                stock_bars=stock_bars,
                benchmark_bars=benchmark_bars,
                catalysts=catalysts,
                sector_tracker_info=sector_info,
                sector=sector,
                direction_hint=direction_hint,
            )

            if sig is None:
                log.debug("No signal generated for %s — skipped", symbol)
                continue

            result = state.order_manager.execute_signal(sig, account, bot_positions)
            if not result.success:
                log.info("Order rejected for %s: %s", symbol, result.reason)
                continue

            # Open position in tracker
            state.tracker.open_position(
                symbol=symbol,
                direction=sig.direction,
                shares=result.shares,
                entry_price=result.entry_price,
                entry_time=int(time.time()),
                stop_price=result.stop_price,
                current_price=result.entry_price,
                sector=sector,
            )

            # Log entry
            state.trade_logger.record_entry(sig, result.shares)

            # Subscribe streaming
            subscribe_streaming(state, symbol)

            # Notify
            await notify_entry(
                symbol=symbol,
                direction=sig.direction,
                shares=result.shares,
                entry_price=result.entry_price,
                stop_price=result.stop_price,
                signal_score=sig.score,
                regime=regime,
            )

            log.info(
                "ENTRY %s %s %d @ %.2f  stop=%.2f  score=%.2f",
                sig.direction, symbol, result.shares,
                result.entry_price, result.stop_price, sig.score,
            )

        except Exception:
            log.exception("Error processing candidate %s", symbol)


# ---------------------------------------------------------------------------
# Loop: sector regime scanner (5 min)
# ---------------------------------------------------------------------------


async def sector_regime_scanner_loop(state: SharedState) -> None:
    """Sector bars → regime → scanner → ranking → signal gen + execution."""
    while not state.shutdown:
        try:
            if not (is_market_hours() or is_premarket()):
                await asyncio.sleep(60)
                continue

            t0 = time.monotonic()

            # Sector tracker refresh
            await state.sector_tracker.fetch_sector_bars(state.ib, days=65)
            log.info("Sector bars refreshed")

            # Regime
            await state.regime_detector.fetch_market_data(state.ib, days=65)
            regime = state.regime_detector.classify_regime()
            async with state.pool.acquire() as conn:
                await state.regime_detector.log_regime(conn)
            log.info("Regime: %s", regime)

            if is_market_hours():
                # Scanner
                await state.scanner.run_all_scans(state.ib)
                log.info("Market scans complete")

                # Ranking
                candidates = state.ranker.rank_candidates()
                log.info("Ranked %d candidates", len(candidates))

                # Signal → Execution
                await process_candidates(state, candidates)

            elapsed = time.monotonic() - t0
            log.info("Sector/scan cycle took %.1fs", elapsed)

        except Exception:
            log.exception("Error in sector_regime_scanner_loop")

        await asyncio.sleep(300)  # 5 min


# ---------------------------------------------------------------------------
# Loop: news (2 min market / 30 min off)
# ---------------------------------------------------------------------------


async def news_loop(state: SharedState) -> None:
    while not state.shutdown:
        try:
            new = await state.catalyst_engine.poll_feeds()
            if new:
                log.info("Ingested %d new catalysts", len(new))
        except Exception:
            log.exception("Error in news_loop")

        interval = 120 if is_market_hours() else 1800
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Loop: position management (30 sec)
# ---------------------------------------------------------------------------


async def position_management_loop(state: SharedState) -> None:
    """Update prices, check trailing stops, partial exits, time decay."""
    while not state.shutdown:
        try:
            if not is_market_hours():
                await asyncio.sleep(60)
                continue

            positions = state.tracker.positions
            if not positions:
                await asyncio.sleep(30)
                continue

            regime = state.regime_detector.get_current_regime() or "choppy"
            now_ts = int(time.time())

            # Pre-compute ATR for all open positions
            atrs: dict[str, float] = {}
            try:
                async with state.pool.acquire() as conn:
                    for symbol in positions:
                        rows = await query_bars(conn, symbol=symbol, timeframe="1d")
                        if rows and len(rows) >= 15:
                            highs = [r["high"] for r in rows[-20:]]
                            lows = [r["low"] for r in rows[-20:]]
                            closes = [r["close"] for r in rows[-20:]]
                            atr_vals = compute_atr(highs, lows, closes, period=14)
                            last_atr = next((v for v in reversed(atr_vals) if v is not None), None)
                            if last_atr:
                                atrs[symbol] = last_atr
            except Exception:
                log.warning("Failed to compute ATRs from DB, using fallback", exc_info=True)

            for symbol, pos in list(positions.items()):
                price = get_streaming_price(state, symbol)
                if price is None:
                    continue

                state.tracker.update_price(symbol, price)

                # ATR from pre-computed values, fallback to 2% of price
                atr = atrs.get(symbol, pos.current_price * 0.02)

                # Trailing stop
                stop_update = state.trailing_stop.update_stop(pos, price, atr, regime)
                if stop_update.moved:
                    state.tracker.update_stop(symbol, stop_update.new_stop)
                    log.debug("Trailing stop %s: %.2f → %.2f", symbol, stop_update.old_stop, stop_update.new_stop)

                # Check if stop hit
                if pos.direction == "LONG" and price <= state.tracker.get(symbol).stop_price:
                    await close_and_log(state, symbol, price, "trailing_stop")
                    continue
                if pos.direction == "SHORT" and price >= state.tracker.get(symbol).stop_price:
                    await close_and_log(state, symbol, price, "trailing_stop")
                    continue

                # Partial exit
                partial = state.partial_exit.check(pos, price, atr)
                if partial:
                    log.info("Partial exit tier %d for %s: %d shares", partial.tier, symbol, partial.shares_to_sell)
                    pnl = state.tracker.reduce_shares(symbol, partial.shares_to_sell, price, now_ts)
                    async with state.pool.acquire() as conn:
                        closed = state.tracker.closed_trades[-1] if state.tracker.closed_trades else None
                        if closed:
                            await state.trade_logger.record_exit(conn, closed, f"partial_t{partial.tier}")

                # Time decay
                decay = state.time_decay.check(pos, price, atr, now_ts)
                if decay:
                    if decay.action == "exit":
                        await close_and_log(state, symbol, price, f"time_decay: {decay.reason}")
                    elif decay.action == "tighten_stop" and decay.new_stop is not None:
                        state.tracker.update_stop(symbol, decay.new_stop)

        except Exception:
            log.exception("Error in position_management_loop")

        await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# Loop: risk (30 sec)
# ---------------------------------------------------------------------------


async def risk_loop(state: SharedState) -> None:
    while not state.shutdown:
        try:
            if not is_market_hours():
                await asyncio.sleep(60)
                continue

            account = await build_account_state(state.ib, state.tracker)
            bot_positions = state.tracker.positions
            status = state.risk_controller.evaluate(account, bot_positions)

            if status.warnings:
                log.warning("Risk warnings: %s", status.warnings)
                await notify_risk(status.warnings, status.halt_trading)

            if status.halt_trading:
                log.warning("RISK HALT triggered")

        except Exception:
            log.exception("Error in risk_loop")

        await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# Loop: account snapshot (5 min)
# ---------------------------------------------------------------------------


async def account_snapshot_loop(state: SharedState) -> None:
    while not state.shutdown:
        try:
            if not is_market_hours():
                await asyncio.sleep(300)
                continue

            account = await build_account_state(state.ib, state.tracker)
            heat = state.risk_controller.calc_portfolio_heat(
                state.tracker.positions, account.net_liquidation
            )
            async with state.pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO account_snapshots "
                    "(timestamp, net_liquidation, cash, buying_power, "
                    "day_trades_remaining, daily_pnl, open_positions, portfolio_heat) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    int(time.time()),
                    account.net_liquidation,
                    account.total_cash_value,
                    account.buying_power,
                    account.day_trades_remaining,
                    account.daily_pnl,
                    len(state.tracker),
                    heat,
                )
            log.debug("Account snapshot saved")

        except Exception:
            log.exception("Error in account_snapshot_loop")

        await asyncio.sleep(300)


# ---------------------------------------------------------------------------
# Loop: EOD tasks (reconciliation + daily report)
# ---------------------------------------------------------------------------


async def eod_loop(state: SharedState) -> None:
    recon_done_today = False
    report_done_today = False

    while not state.shutdown:
        try:
            now = _now_et()
            today = now.date()

            # Reset flags at midnight
            if now.hour < 1:
                recon_done_today = False
                report_done_today = False

            # Reconciliation at 16:05
            if not recon_done_today and _is_after(16, 5) and now.weekday() < 5:
                recon_done_today = True
                bot_shares = state.tracker.as_share_counts()
                ibkr_positions = {}
                for pos in state.ib.positions():
                    ibkr_positions[pos.contract.symbol] = int(pos.position)

                snapshot = state.reconciler.run(bot_shares, ibkr_positions)
                if not snapshot.matches:
                    log.error("Reconciliation FAILED: %s", snapshot.mismatches)
                    await notify_reconciliation_fail(snapshot.mismatches)
                else:
                    log.info("Reconciliation passed")

            # Daily report at 16:15
            if not report_done_today and _is_after(16, 15) and now.weekday() < 5:
                report_done_today = True
                try:
                    report = await send_daily_report(
                        state.tracker,
                        state.risk_controller,
                        state.reconciler,
                        equity=float(risk_config.strategy_capital),
                    )
                    log.info("Daily report sent")
                except Exception:
                    log.exception("Failed to send daily report")

        except Exception:
            log.exception("Error in eod_loop")

        await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Warmup timer
# ---------------------------------------------------------------------------


async def warmup_timer(state: SharedState) -> None:
    """Enable trading after 30 min warmup (10:00 ET) on market days."""
    while not state.shutdown:
        if is_market_hours() and _is_after(10, 0):
            if not state.warmup_done:
                state.warmup_done = True
                log.info("Warmup complete — trading enabled")
            await asyncio.sleep(300)
        else:
            # Reset for next day
            if not is_market_hours():
                state.warmup_done = False
            await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    setup_logging()
    log.info("Starting agentic_investment orchestrator")

    # Connect IB
    ib = await connect_ib()

    # Init DB
    await init_db()
    pool = await asyncpg.create_pool(DATABASE_URL)

    # Load protected positions
    protected = load_protected_positions()
    log.info("Loaded %d protected positions", len(protected))

    # Init components
    sector_tracker = SectorTracker()
    regime_detector = RegimeDetector()
    scanner = MarketScanner(sector_tracker)
    ranker = CandidateRanker(scanner, sector_tracker, regime_detector)
    catalyst_engine = CatalystEngine()
    order_manager = OrderManager(ib, risk_config)
    tracker = PositionTracker()
    risk_controller = RiskController(risk_config)
    reconciler = Reconciler(protected)
    trade_logger = TradeLogger()
    trailing_stop = TrailingStopManager(risk_config)
    partial_exit = PartialExitManager(risk_config)
    time_decay_mgr = TimeDecayManager(risk_config)

    state = SharedState(
        ib=ib,
        pool=pool,
        sector_tracker=sector_tracker,
        regime_detector=regime_detector,
        scanner=scanner,
        ranker=ranker,
        catalyst_engine=catalyst_engine,
        order_manager=order_manager,
        tracker=tracker,
        risk_controller=risk_controller,
        reconciler=reconciler,
        trade_logger=trade_logger,
        trailing_stop=trailing_stop,
        partial_exit=partial_exit,
        time_decay=time_decay_mgr,
        protected=protected,
    )

    # Start dashboard
    app = create_app(tracker, risk_controller, reconciler, equity=risk_config.strategy_capital)
    start_dashboard(app, host="0.0.0.0", port=8080)
    log.info("Dashboard started on http://0.0.0.0:8080")

    # Bootstrap data
    try:
        await bootstrap_data(state)
    except Exception:
        log.exception("Data bootstrap failed — continuing with available data")

    # Initial sector/regime fetch
    try:
        await sector_tracker.fetch_sector_bars(ib, days=65)
        await regime_detector.fetch_market_data(ib, days=65)
        regime_detector.classify_regime()
        log.info("Initial regime: %s", regime_detector.get_current_regime())
    except Exception:
        log.exception("Initial sector/regime fetch failed")

    # Launch loops
    tasks = [
        asyncio.create_task(sector_regime_scanner_loop(state), name="sector_scan"),
        asyncio.create_task(news_loop(state), name="news"),
        asyncio.create_task(position_management_loop(state), name="pos_mgmt"),
        asyncio.create_task(risk_loop(state), name="risk"),
        asyncio.create_task(account_snapshot_loop(state), name="account_snap"),
        asyncio.create_task(eod_loop(state), name="eod"),
        asyncio.create_task(warmup_timer(state), name="warmup"),
    ]

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    for sig_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig_name, lambda: asyncio.create_task(_shutdown(state, tasks)))

    log.info("All loops started — entering main wait")
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass

    # Cleanup
    ib.disconnect()
    await pool.close()
    await close_pool()
    log.info("Shutdown complete")


async def _shutdown(state: SharedState, tasks: list[asyncio.Task]) -> None:
    log.info("Shutdown signal received")
    state.shutdown = True
    for t in tasks:
        t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
