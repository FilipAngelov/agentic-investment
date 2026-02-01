# Development Plan

Phased development plan for the Agentic Investment trading system. Each sub-task is designed to be independently testable.

---

## Phase 0: Infrastructure & Foundation (Complexity: Low)

| Task | Description | Deliverable |
|------|-------------|-------------|
| ~~0.1~~ | ~~IBKR paper account + IB Gateway setup~~ | ~~Manual — Gateway running on Mac Mini, paper mode~~ ✅ |
| ~~0.2~~ | ~~Project scaffolding — Poetry, pyproject.toml, directory structure~~ | ~~This commit~~ ✅ |
| ~~0.3~~ | ~~Basic ib_async connection — connect to Gateway, fetch account info~~ | ~~`main.py` prints account summary~~ ✅ |
| ~~0.4~~ | ~~Config module — settings, env vars, `starting_portfolio.csv` loader~~ | ~~`config/settings.py` tested~~ ✅ |
| ~~0.5~~ | ~~Protected positions guard — load CSV, enforce sell protection~~ | ~~Unit tests passing~~ ✅ |
| ~~0.6~~ | ~~SQLite setup — create schema (bars, catalysts, trades, account_snapshots, regime_log, sector_snapshots)~~ | ~~`data/store.py` with migrations~~ ✅ |
| ~~0.7~~ | ~~Data models (Pydantic or dataclasses for Bar, Trade, Signal, Position, AccountState)~~ | ~~`data/models.py` tested~~ ✅ |

---

## Phase 1: Market Scanner (Complexity: Medium)

Depends on: Phase 0

| Task | Description | Deliverable |
|------|-------------|-------------|
| 1.1 | ~~Sector tracker — monitor 11 sector ETFs, momentum scoring~~ | ~~`scanner/sectors.py`~~ ✅ |
| 1.2 | ~~Market regime detection — SPY/VIX-based classification (strong_bull/bull/choppy/bear/crisis)~~ | ~~Regime classifier with unit tests~~ ✅ |
| ~~1.3~~ | ~~IBKR market scanner integration — top gainers/losers, unusual volume~~ | ~~`scanner/screener.py`~~ ✅ |
| ~~1.4~~ | ~~Stock screener — candidate ranking with composite scores~~ | ~~Ranked watchlist output~~ ✅ |
| ~~1.5~~ | ~~News & catalyst engine — RSS/API ingestion, basic NLP classification~~ | ~~`scanner/news.py`~~ ✅ |
| ~~1.6~~ | ~~Data ingestion layer — historical bar fetching, incremental updates, rate limiting~~ | ~~`data/ingest.py`~~ ✅ |

---

## Phase 2: Signal Generator (Complexity: Medium-High)

Depends on: Phase 1

| Task | Description | Deliverable |
|------|-------------|-------------|
| ~~2.1~~ | ~~Technical analysis — breakout detection, RSI, MACD, rate of change~~ | ~~`data/indicators.py` + `signals/technical.py`~~ ✅ |
| ~~2.2~~ | ~~Volume confirmation logic~~ | ~~Volume rules integrated into signals~~ ✅ |
| ~~2.3~~ | ~~Multi-timeframe alignment (5m, 15m, daily)~~ | ~~`signals/timeframe.py`~~ ✅ |
| ~~2.4~~ | ~~Expected Move (EM) calculation — CSS × beta × HV × regime factor~~ | ~~EM calculator with tests~~ ✅ |
| ~~2.5~~ | ~~Composite signal scoring — sector momentum + news + technicals + volume~~ | ~~`signals/scoring.py`~~ ✅ |
| ~~2.6~~ | ~~Signal output: direction, entry, stop, target, confidence~~ | ~~Signal dataclass finalized~~ ✅ |

---

## Phase 3: Execution Engine (Complexity: High)

Depends on: Phase 2

| Task | Description | Deliverable |
|------|-------------|-------------|
| ~~3.1~~ | ~~DTBP Guard — pre-trade compliance (DTBP check, margin impact, protected shares)~~ | ~~`execution/dtbp_guard.py` with full tests~~ ✅ |
| ~~3.2~~ | ~~Position sizing — risk-per-trade / distance-to-stop~~ | ~~`execution/sizing.py`~~ ✅ |
| ~~3.3~~ | ~~Order manager — limit/market orders via ib_async, stop placement~~ | ~~`execution/orders.py`~~ ✅ |
| ~~3.4~~ | ~~Adaptive trailing stop algorithm (ATR-based, regime-adjusted)~~ | ~~`execution/trailing_stop.py`~~ ✅ |
| ~~3.5~~ | ~~Partial exit logic — scale out at +1 ATR, +2 ATR~~ | ~~`execution/partial_exit.py`~~ ✅ |
| ~~3.6~~ | ~~Time decay exits~~ | ~~`execution/time_decay.py`~~ ✅ |
| ~~3.7~~ | ~~Risk controller — daily drawdown kill switch, portfolio heat, sector concentration~~ | ~~`execution/risk.py`~~ ✅ (Note: concentration uses sector-count proxy; full Pearson correlation deferred until historical bar pipeline is available) |
| 3.8 | Daily reconciliation — bot positions + protected = IBKR total | Reconciliation check in risk.py |

---

## Phase 4: Portfolio & Monitoring (Complexity: Medium)

Depends on: Phase 3

| Task | Description | Deliverable |
|------|-------------|-------------|
| 4.1 | Position tracker — real-time P&L | `portfolio/tracker.py` |
| 4.2 | Trade logging with full context | Structured trade log |
| 4.3 | Performance analytics — Sharpe, win rate, profit factor, per-sector stats | `portfolio/analytics.py` |
| 4.4 | Notification agent — WhatsApp alerts via API | `portfolio/notify.py` |
| 4.5 | FastAPI dashboard — health checks, position view, P&L summary | Dashboard endpoint |
| 4.6 | Daily/weekly reports | Automated report generation |

---

## Phase 5: Backtest & Graduation (Complexity: High)

Depends on: Phase 4

| Task | Description | Deliverable |
|------|-------------|-------------|
| 5.1 | Event-driven backtester with realistic fill model | `backtest.py` |
| 5.2 | Walk-forward optimization protocol | Optimization harness |
| 5.3 | Paper trading — 30+ day graduation criteria | Graduation dashboard |
| 5.4 | Live deployment — staged capital ramp (25% → 50% → 75% → 100%) | Deployment runbook |
