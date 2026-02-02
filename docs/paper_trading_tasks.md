# Paper Trading — Remaining Tasks

Instructions for Claude Code to complete the system for live paper trading on IBKR.

**Current state:** All modules (scanner, signals, execution, portfolio, backtest) are built and unit-tested. `main.py` is a Phase 0 stub that connects, prints account info, and exits. There is no orchestrator loop. PostgreSQL is initialized with all tables.

**Goal:** A single `python main.py` that runs the full trading system on paper account DUH465831 (port 4002) during market hours, with a dashboard on localhost and WhatsApp notifications via Clawdbot.

---

## Prerequisites (already done — do NOT redo)

- PostgreSQL running, database `agentic_investment` with all tables created
- `.env` file at project root with IB Gateway connection details
- `config/starting_portfolio.csv` populated
- IB Gateway paper mode available at `127.0.0.1:4002`
- Python 3.14 venv at `.venv/` with all dependencies installed

---

## Task 1: Build the Orchestrator (`main.py`) ✅ DONE

Replace the current `main.py` stub with the full orchestrator loop. This is the core of the system.

### Requirements

1. **Connect to IB Gateway** via `ib_async` (already done in current stub — reuse the connection logic).

2. **Initialize all components** on startup:
   - `SectorTracker` — fetch initial sector bars (65 days)
   - `RegimeDetector` — fetch SPY/VIX data, classify initial regime
   - `MarketScanner` — instantiate with SectorTracker reference
   - `CandidateRanker` — instantiate with Scanner, SectorTracker, RegimeDetector
   - `CatalystEngine` — instantiate with LLM API key from settings
   - `OrderManager` — instantiate with IB connection + RiskConfig
   - `PositionTracker` — empty on startup (no bot positions yet)
   - `RiskController` — instantiate with RiskConfig
   - `Reconciler` — instantiate with `load_protected_positions()`
   - `TradeLogger` — instantiate
   - `PositionSizer` — already inside OrderManager, no separate init needed
   - Get a `asyncpg.Pool` connection to PostgreSQL (`DATABASE_URL` from settings)

3. **Market hours detection.** All scheduled loops should only run during US market hours (9:30 AM – 4:00 PM ET, weekdays). Outside market hours, the system should idle (sleep and check every 60 seconds). Pre-market data fetch (sector bars, regime) can start at 9:00 AM ET.

4. **Agent scheduling loop.** Use `asyncio` tasks with these intervals (from the design doc):

   | Agent / Task | Interval | Implementation |
   |---|---|---|
   | **Sector Tracker refresh** | Every 5 min during market hours | Call `sector_tracker.fetch_sector_bars(ib, days=65)` then `sector_tracker.compute_snapshots()` and persist to DB |
   | **Regime Detector refresh** | Every 5 min during market hours | Call `regime_detector.fetch_market_data(ib)` then `regime_detector.classify_regime()`, log to `regime_log` table |
   | **Market Scanner** | Every 5 min during market hours | Run `scanner.run_all_scans(ib)` |
   | **Candidate Ranking** | Every 5 min (after scanner completes) | Run `ranker.rank_candidates()` |
   | **News/Catalyst poll** | Every 2 min during market hours, every 30 min off-hours | Run `catalyst_engine.poll_feeds()` then `catalyst_engine.classify_pending()` |
   | **Signal generation** | After each candidate ranking cycle | For each top candidate (top 10 by score), run `signals.pipeline.generate_signal()` |
   | **Order execution** | On each valid signal | Run `order_manager.execute_signal()` — this already does sizing + DTBP guard + bracket order |
   | **Position management** | Every 30 sec during market hours | For each open position: update price, check trailing stop, check partial exits, check time decay |
   | **Risk monitoring** | Every 30 sec during market hours | Run `risk_controller.evaluate()` — if halted, stop all new orders |
   | **Reconciliation** | Once at 4:05 PM ET | Run `reconciler.run()` comparing bot positions + protected vs IBKR positions |
   | **Daily report** | Once at 4:15 PM ET | Generate and send daily report via WhatsApp |
   | **Account snapshot** | Every 5 min during market hours | Query IBKR account summary, insert into `account_snapshots` table |

5. **Data flow between agents.** The agents are NOT independent processes — they share state in-memory:
   - `SectorTracker` instance is shared by `MarketScanner`, `CandidateRanker`, and signal generation
   - `RegimeDetector` instance is shared by `CandidateRanker` and signal generation
   - `PositionTracker` is the single source of truth for open bot positions
   - `RiskController.is_halted` gates all new order submissions
   - `Reconciler.is_halted` also gates all new order submissions

6. **Graceful shutdown.** On SIGINT/SIGTERM:
   - Cancel all asyncio tasks
   - Disconnect from IB Gateway
   - Close PostgreSQL pool
   - Log final state

### Architecture pattern

```python
async def main():
    ib = IB()
    await ib.connectAsync(...)
    pool = await asyncpg.create_pool(DATABASE_URL)
    
    # Init all components
    ...
    
    # Start dashboard in background thread
    start_dashboard_thread(tracker, risk_controller, reconciler)
    
    # Create asyncio tasks
    tasks = [
        asyncio.create_task(sector_loop(...)),
        asyncio.create_task(regime_loop(...)),
        asyncio.create_task(scanner_loop(...)),
        asyncio.create_task(news_loop(...)),
        asyncio.create_task(position_management_loop(...)),
        asyncio.create_task(risk_loop(...)),
        asyncio.create_task(account_snapshot_loop(...)),
        asyncio.create_task(eod_tasks_loop(...)),  # reconciliation + daily report
    ]
    
    # Wait forever (or until signal)
    await asyncio.gather(*tasks)
```

Each loop function should:
- Check if within market hours before doing work
- Sleep for the configured interval
- Catch and log exceptions without crashing (retry on next cycle)
- Log timing info (how long each cycle took)

---

## Task 2: Position Management Loop ✅ DONE

This is the most critical runtime loop. Every 30 seconds during market hours, for each open position in `PositionTracker`:

1. **Update current price** — query IBKR for latest market data (`ib.reqMktData()` or `ib.reqTickers()`). Update `position.current_price`.

2. **Check trailing stop** — use `execution/trailing_stop.py`. The trailing stop module needs the current ATR (from `data/indicators.py`) and the regime factor. If stop is hit, submit a market sell order via `order_manager` (or directly via ib_async) and close the position in `PositionTracker`.

3. **Check partial exits** — use `execution/partial_exit.py`. If profit thresholds are hit and partial exit hasn't been taken yet, sell the partial amount.

4. **Check time decay** — use `execution/time_decay.py`. If the trade has stalled, tighten stop to breakeven or exit.

5. **Log trade exits** — when a position closes (stop hit, partial exit, time decay), call `trade_logger.record_exit()` to persist to PostgreSQL.

### Key detail: price streaming

For open positions, use `ib.reqMktData(contract, genericTickList="", snapshot=False)` to get streaming data. Subscribe when a position opens, unsubscribe when it closes. Use `ib.ticker(contract)` to read latest price without polling.

For the 30-second management loop, just read from the already-streaming tickers.

---

## Task 3: Signal → Execution Pipeline Wiring ✅ DONE

The signal pipeline exists (`signals/pipeline.py`) and the order manager exists (`execution/orders.py`), but they're not connected in a loop. Wire them:

1. After `CandidateRanker.rank_candidates()` returns the top candidates:
2. For each candidate, fetch the required data:
   - Daily bars from DB (for the stock and SPY) — needed for EM calculator and beta
   - Recent catalysts from DB — `query_catalysts(conn, symbol=symbol, since=...)`
   - Sector tracker info — `sector_tracker.compute_momentum(etf)`, `sector_tracker.compute_relative_strength(etf)`
   - Higher timeframe bars if available
3. Call `generate_signal(bars, regime, stock_bars, benchmark_bars, catalysts, sector_tracker_info, ...)`
4. If signal is not None, call `order_manager.execute_signal(signal, account_state, bot_positions)`
5. If order succeeds, call `tracker.open_position(...)` and `trade_logger.record_entry(...)`

### Building AccountState

`OrderManager.execute_signal()` needs an `AccountState` object. Build it from IBKR account summary:

```python
account_values = await ib.accountSummaryAsync()
# Parse into AccountState(net_liquidation=..., buying_power=..., 
#   available_funds=..., excess_liquidity=..., day_trades_remaining=...,
#   daily_pnl=..., strategy_deployed=tracker.deployed_capital)
```

Check `data/models.py` for the exact `AccountState` fields.

---

## Task 4: Data Ingestion Bootstrapping ✅ DONE

On startup (before the main loop), the system needs historical data in PostgreSQL for indicators to work:

1. **Daily bars** — For all sector ETFs + SPY + VIX, fetch 500 days of daily bars using `data/ingest.py`. This is needed for SMA(50), momentum calculations, etc.

2. **5-minute bars** — For sector ETFs, fetch 5 days of 5-min bars. Needed for intraday signal generation.

3. **Incremental updates** — During the trading day, after each scanner cycle, ingest recent bars for any new candidate symbols that don't have data in DB yet. Use `ingest.fetch_and_store()` with appropriate lookback.

4. The ingestion module (`data/ingest.py`) already has rate limiting and dedup. Use it as-is.

### Important: warm-up period

On the very first run, the bot should NOT trade for the first 30 minutes (9:30–10:00 AM ET). Use this time to:
- Fetch historical data
- Let sector tracker and regime detector populate
- Let scanner run at least one full cycle
- Let news engine collect initial catalysts

After warm-up, enable signal generation and execution.

---

## Task 5: WhatsApp Notifications via Clawdbot ✅ DONE

The current `portfolio/notify.py` uses CallMeBot (a third-party WhatsApp API). Replace this with Clawdbot's REST API which is already running on this machine.

### Changes to `portfolio/notify.py`

Replace the CallMeBot implementation with a simple POST to Clawdbot's local API:

```python
CLAWDBOT_URL = os.getenv("CLAWDBOT_API_URL", "http://127.0.0.1:3007")
CLAWDBOT_TOKEN = os.getenv("CLAWDBOT_API_TOKEN", "")
NOTIFY_TARGET = os.getenv("NOTIFY_TARGET", "+38978345900")
```

Send messages by POSTing to Clawdbot's send endpoint. Check the Clawdbot API docs at `/opt/homebrew/lib/node_modules/clawdbot/docs/` for the exact endpoint format. The gateway listens on port 3007 by default.

If the Clawdbot API is not documented well enough, fall back to writing notification text to a file (`/Users/filip/clawd/notifications.log`) that Cdius can pick up and forward. This is a pragmatic fallback.

### Update `.env`

Add to `.env`:
```
CLAWDBOT_API_URL=http://127.0.0.1:3007
CLAWDBOT_API_TOKEN=<token from gateway config>
NOTIFY_TARGET=+38978345900
```

### Update `config/settings.py` NotifyConfig

Replace the CallMeBot fields with Clawdbot fields.

---

## Task 6: Dashboard Startup ✅ DONE

The FastAPI dashboard (`portfolio/dashboard.py`) is built but not started from `main.py`. Add it:

1. Run the dashboard in a **background thread** (not asyncio — uvicorn has its own event loop management):

```python
from portfolio.dashboard import create_app, start_in_thread

def start_dashboard_thread(tracker, risk_controller, reconciler):
    app = create_app(tracker, risk_controller, reconciler, equity=10_000.0)
    thread = threading.Thread(
        target=uvicorn.run, 
        args=(app,), 
        kwargs={"host": "0.0.0.0", "port": 8080, "log_level": "warning"},
        daemon=True,
    )
    thread.start()
```

2. Dashboard should be accessible at `http://localhost:8080`.

---

## Task 7: Logging ✅ DONE

Set up proper Python logging for the entire application:

1. Create `config/logging.py` with a standard config:
   - Console handler: INFO level, concise format
   - File handler: DEBUG level, to `logs/trading.log`, with rotation (10MB, 5 backups)
   - Per-module loggers (scanner, signals, execution, portfolio, data)

2. Every agent loop should log:
   - Start/end of each cycle with duration
   - Any errors (with traceback)
   - Key decisions (regime change, signal generated, order placed, stop hit)

3. Create `logs/` directory if it doesn't exist.

---

## Task 8: LLM API Key ✅ DONE

The `CatalystEngine` in `scanner/news.py` uses the Anthropic SDK to classify news. The `.env` has `LLM_API_KEY=` (empty).

Set it to use the same Anthropic API key that Clawdbot uses. Check `~/.clawdbot/.env` for `ANTHROPIC_API_KEY` and copy it into the project's `.env` as `LLM_API_KEY`.

If no key is available, the news engine should degrade gracefully — skip LLM classification and just store raw headlines with `sentiment=None`, `magnitude=None`. The system should still trade based on technical signals alone.

---

## Task 9: Run Tests ✅ DONE

Before making any changes, run the existing test suite:

```bash
cd /Users/filip/dev/agentic_investment
.venv/bin/python -m pytest tests/ -v --tb=short 2>&1 | head -200
```

Fix any failures. All existing tests must pass before adding new code.

After implementing the orchestrator, add integration tests:
- `tests/test_orchestrator.py` — mock IB connection, verify the startup sequence, verify market hours detection, verify graceful shutdown
- Test that the position management loop correctly processes trailing stops and partial exits

---

## Task 10: First Run Checklist

After all tasks above are complete, this is the manual verification sequence:

1. Start IB Gateway in paper mode (port 4002)
2. Run `python main.py`
3. Verify it connects to IB Gateway successfully
4. Verify it fetches initial data (sector bars, regime, etc.)
5. Verify dashboard is accessible at `http://localhost:8080/api/health`
6. Verify no errors in `logs/trading.log`
7. Wait for first scanner cycle — check that candidates appear in logs
8. If during market hours, watch for signal generation
9. Monitor for 1 hour, check no crashes

---

## Execution Order

Do these in order — each task depends on the previous:

1. **Task 9** — Run tests first, fix any broken ones
2. **Task 7** — Logging (needed by everything)
3. **Task 8** — LLM API key (so news engine works)
4. **Task 5** — Notifications (so we get alerts)
5. **Task 1** — Orchestrator (the big one)
6. **Task 4** — Data ingestion bootstrap (called by orchestrator on startup)
7. **Task 2** — Position management loop (part of orchestrator)
8. **Task 3** — Signal → execution wiring (part of orchestrator)
9. **Task 6** — Dashboard startup (part of orchestrator)
10. **Task 10** — Manual verification

---

## Important Rules

- **Never touch `config/starting_portfolio.csv`** — these positions are sacred
- **Never connect to port 4001** — that's the live account. Always use port 4002 (paper)
- **All new files use snake_case** naming
- **Run tests after each task** to catch regressions
- **Do not install new dependencies** without explicit approval — everything needed is already in `pyproject.toml`
- **Log everything** — when in doubt, log it at DEBUG level
- If IB Gateway is not running, `main.py` should retry connection every 30 seconds (not crash)
