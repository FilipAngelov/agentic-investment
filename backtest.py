"""Backtesting harness: event-driven simulation.

Usage:
    poetry run python backtest.py --start 2024-01-01 --end 2024-12-31 \
        --capital 10000 --slippage-bps 5 --universe-size 50 --output results/
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

from backtest.data_feed import HistoricalDataFeed
from backtest.engine import BacktestConfig, BacktestEngine
from config.settings import RiskConfig
from data.store import get_db

logger = logging.getLogger(__name__)


def _parse_date_to_ts(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


async def main(args: argparse.Namespace) -> None:
    start_ts = _parse_date_to_ts(args.start)
    end_ts = _parse_date_to_ts(args.end) + 86400 - 1  # end of day

    risk_config = RiskConfig(strategy_capital=args.capital)
    config = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        slippage_bps=args.slippage_bps,
        commission_per_share=args.commission,
        partial_fill_prob=args.partial_fill_prob,
        universe_size=args.universe_size,
        risk_config=risk_config,
        primary_timeframe=args.timeframe,
        output_dir=args.output,
    )

    feed = HistoricalDataFeed()

    logger.info("Loading bars from database...")
    async with get_db() as conn:
        # Get all symbols that have bars in the date range
        rows = await conn.fetch(
            "SELECT DISTINCT symbol FROM bars WHERE timestamp >= $1 AND timestamp <= $2",
            start_ts, end_ts,
        )
        symbols = [r["symbol"] for r in rows]
        logger.info("Found %d symbols with data", len(symbols))

        timeframes = ["1d", config.primary_timeframe]
        if "15m" not in timeframes:
            timeframes.append("15m")

        # Load with extra lookback for indicators
        lookback_ts = start_ts - 90 * 86400
        await feed.load(conn, symbols, timeframes, lookback_ts, end_ts)

    logger.info("Running backtest %s to %s...", args.start, args.end)
    engine = BacktestEngine(config, feed)
    results = await engine.run()

    print(results.summary())

    os.makedirs(args.output, exist_ok=True)
    eq_path = os.path.join(args.output, "equity_curve.csv")
    trades_path = os.path.join(args.output, "trades.csv")
    results.equity_curve_csv(eq_path)
    results.trades_csv(trades_path)
    logger.info("Wrote %s and %s", eq_path, trades_path)


def cli() -> None:
    parser = argparse.ArgumentParser(description="Event-driven backtester")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=10_000, help="Initial capital")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="Slippage in basis points")
    parser.add_argument("--commission", type=float, default=0.005, help="Commission per share")
    parser.add_argument("--partial-fill-prob", type=float, default=0.05, help="Partial fill probability")
    parser.add_argument("--universe-size", type=int, default=50, help="Number of symbols in universe")
    parser.add_argument("--timeframe", default="5m", help="Primary intraday timeframe")
    parser.add_argument("--output", default="results", help="Output directory for CSVs")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    asyncio.run(main(args))


if __name__ == "__main__":
    cli()
