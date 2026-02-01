"""CLI entry point for walk-forward optimization."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import asyncpg

from backtest.data_feed import HistoricalDataFeed
from backtest.optimizer import WFOConfig, WalkForwardOptimizer
from backtest.param_space import ParamSpace
from config.settings import DATABASE_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-Forward Optimization")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--test-months", type=int, default=3)
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--capital", type=float, default=10_000)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--output", default="results/wfo/")
    args = parser.parse_args()

    start_ts = int(datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

    # Load data
    logger.info("Connecting to database and loading bars...")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Get all symbols with bars in range
        rows = await conn.fetch(
            "SELECT DISTINCT symbol FROM bars WHERE timestamp >= $1 AND timestamp <= $2",
            start_ts, end_ts,
        )
        symbols = [r["symbol"] for r in rows]
        logger.info("Found %d symbols", len(symbols))

        feed = HistoricalDataFeed()
        await feed.load(conn, symbols, ["1d", "5m"], start_ts, end_ts)
    finally:
        await conn.close()

    logger.info("Loaded feed with %d daily timestamps", len(feed.daily_timestamps()))

    wfo_config = WFOConfig(
        train_months=args.train_months,
        test_months=args.test_months,
        n_samples=args.samples,
        seed=args.seed,
        initial_capital=args.capital,
        slippage_bps=args.slippage_bps,
    )
    param_space = ParamSpace.default()
    optimizer = WalkForwardOptimizer(wfo_config, param_space, feed)

    logger.info("Running walk-forward optimization...")
    results = await optimizer.run()

    print(results.summary())

    os.makedirs(args.output, exist_ok=True)
    csv_path = os.path.join(args.output, "wfo_folds.csv")
    results.to_csv(csv_path)
    logger.info("Fold results written to %s", csv_path)


if __name__ == "__main__":
    asyncio.run(main())
