"""Walk-forward optimizer: rolling train/test harness using BacktestEngine."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backtest.data_feed import HistoricalDataFeed
from backtest.engine import BacktestConfig, BacktestEngine
from backtest.param_space import ParamSpace
from backtest.wfo_results import FoldResult, WFOResults
from config.settings import RiskConfig

logger = logging.getLogger(__name__)


@dataclass
class WFOConfig:
    train_months: int = 12
    test_months: int = 3
    n_samples: int = 50
    objective: str = "sharpe_ratio"
    seed: int = 42
    initial_capital: float = 10_000
    slippage_bps: float = 5.0


class WalkForwardOptimizer:
    """Rolling walk-forward optimization over historical data."""

    def __init__(
        self,
        wfo_config: WFOConfig,
        param_space: ParamSpace,
        feed: HistoricalDataFeed,
    ) -> None:
        self._wfo = wfo_config
        self._space = param_space
        self._feed = feed

    async def run(self) -> WFOResults:
        """Execute walk-forward optimization across all folds."""
        daily_ts = self._feed.daily_timestamps()
        if not daily_ts:
            logger.warning("No daily timestamps in feed")
            return WFOResults()

        folds = self._generate_folds(daily_ts)
        logger.info("Generated %d WFO folds", len(folds))

        results = WFOResults()
        for i, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
            logger.info(
                "Fold %d: train %s→%s, test %s→%s",
                i, _ts_to_date(tr_s), _ts_to_date(tr_e),
                _ts_to_date(te_s), _ts_to_date(te_e),
            )
            fold = await self._optimize_fold(i, tr_s, tr_e, te_s, te_e)
            results.add_fold(fold)

        return results

    def _generate_folds(
        self, daily_ts: list[int]
    ) -> list[tuple[int, int, int, int]]:
        """Generate (train_start, train_end, test_start, test_end) tuples.

        Roll forward by test_months each step.
        """
        if not daily_ts:
            return []

        first_date = datetime.fromtimestamp(daily_ts[0], tz=timezone.utc)
        last_date = datetime.fromtimestamp(daily_ts[-1], tz=timezone.utc)

        folds: list[tuple[int, int, int, int]] = []
        train_start_date = first_date

        while True:
            train_end_date = _add_months(train_start_date, self._wfo.train_months)
            test_start_date = train_end_date
            test_end_date = _add_months(test_start_date, self._wfo.test_months)

            if test_end_date > last_date:
                break

            tr_s = _find_nearest(daily_ts, int(train_start_date.timestamp()))
            tr_e = _find_nearest(daily_ts, int(train_end_date.timestamp()))
            te_s = _find_nearest(daily_ts, int(test_start_date.timestamp()))
            te_e = _find_nearest(daily_ts, int(test_end_date.timestamp()))

            if tr_s < tr_e and te_s < te_e:
                folds.append((tr_s, tr_e, te_s, te_e))

            # Roll forward by test_months
            train_start_date = _add_months(train_start_date, self._wfo.test_months)

        return folds

    async def _optimize_fold(
        self,
        fold_idx: int,
        train_start: int,
        train_end: int,
        test_start: int,
        test_end: int,
    ) -> FoldResult:
        """Sample params, optimize on train, evaluate best on test."""
        samples = self._space.sample_random(self._wfo.n_samples, self._wfo.seed + fold_idx)

        # Train: evaluate all parameter combos
        best_metric = float("-inf")
        best_params: dict = samples[0] if samples else {}
        best_train_metrics: dict = {}

        for params in samples:
            metrics = await self._run_single(params, train_start, train_end)
            val = metrics.get(self._wfo.objective, 0.0)
            if val > best_metric:
                best_metric = val
                best_params = params
                best_train_metrics = metrics

        # Test: run best params on OOS period
        test_metrics = await self._run_single(best_params, test_start, test_end)

        train_sharpe = best_train_metrics.get("sharpe_ratio", 0.0)
        test_sharpe = test_metrics.get("sharpe_ratio", 0.0)

        # Overfit ratio: avoid division by zero
        if abs(test_sharpe) > 1e-9:
            overfit_ratio = train_sharpe / test_sharpe
        else:
            overfit_ratio = float("inf") if train_sharpe > 0 else 0.0

        return FoldResult(
            fold_index=fold_idx,
            train_start=_ts_to_date(train_start),
            train_end=_ts_to_date(train_end),
            test_start=_ts_to_date(test_start),
            test_end=_ts_to_date(test_end),
            best_params=best_params,
            train_metrics=best_train_metrics,
            test_metrics=test_metrics,
            train_sharpe=train_sharpe,
            test_sharpe=test_sharpe,
            overfit_ratio=overfit_ratio,
        )

    async def _run_single(
        self, params: dict, start_ts: int, end_ts: int
    ) -> dict:
        """Run a single backtest with given params and date range."""
        base_config = BacktestConfig(
            initial_capital=self._wfo.initial_capital,
            slippage_bps=self._wfo.slippage_bps,
            seed=self._wfo.seed,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        config = self._space.apply(params, base_config)
        engine = BacktestEngine(config, self._feed)
        results = await engine.run()
        return results.compute()


def _add_months(dt: datetime, months: int) -> datetime:
    """Add months to a datetime, clamping day to valid range."""
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    import calendar
    max_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, max_day)
    return dt.replace(year=year, month=month, day=day)


def _ts_to_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _find_nearest(sorted_ts: list[int], target: int) -> int:
    """Find the timestamp in sorted_ts nearest to target."""
    import bisect
    idx = bisect.bisect_left(sorted_ts, target)
    if idx == 0:
        return sorted_ts[0]
    if idx >= len(sorted_ts):
        return sorted_ts[-1]
    before = sorted_ts[idx - 1]
    after = sorted_ts[idx]
    return before if (target - before) <= (after - target) else after
