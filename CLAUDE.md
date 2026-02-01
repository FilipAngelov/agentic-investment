# CLAUDE.md — Agentic Investment

## Design Documents
- `docs/agentic_investment.md` — Full strategy, architecture, and constraints
- `docs/development_plan.md` — Phased implementation plan
- `docs/strategy_design.md` — Strategy design (if present)
- `docs/ontology.md` — Sector/news ontology (if present)

## Critical Rules (NEVER violate)

1. **Never sell protected positions.** Load `config/starting_portfolio.csv` at startup. These shares are untouchable. Before any sell: `sellable = current - protected`. If ≤ 0, block the sell.

2. **Never use margin — cash-only trading on a margin account.** Total position value (Filip's holdings + bot trades) must never exceed account cash balance. We use a margin account for benefits (no settlement wait, unlimited day trades) but treat it as cash.

3. **DTBP guard on every order — no exceptions.** Every order passes through `execution/dtbp_guard.py` before submission.

4. **No LLMs in the hot path — pure Python for speed.** LLMs are only used for news sentiment (warm path, async) and supervision (cold path). The trading loop is deterministic Python.

5. **All margin/DTBP calcs are account-wide (not strategy-slice).** IBKR calculates margin on the entire portfolio (Filip's holdings + bot). Query IBKR's actual fields, don't estimate from $10K alone.

6. **Max daily drawdown 2% ($200) — kill switch.** If breached, halt all trading for the day.

7. **Max portfolio heat 6%.** Sum of (position_size × distance_to_stop / equity) across all positions.

8. **Daily reconciliation: bot + protected = IBKR total.** Any mismatch = halt + alert Filip.

## Project Structure
- `config/` — Settings, env vars, sector definitions, protected portfolio
- `scanner/` — Market scanning: sectors, news, screener
- `signals/` — Technical analysis, composite scoring
- `execution/` — DTBP guard, sizing, orders, risk management
- `portfolio/` — Position tracking, notifications, analytics
- `data/` — SQLite persistence, data models, ingestion, indicators
- `tests/` — Unit tests
- `main.py` — Entry point / orchestrator
- `backtest.py` — Backtesting harness
