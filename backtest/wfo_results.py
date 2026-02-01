"""Walk-forward optimization results: per-fold and aggregate metrics."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field


@dataclass
class FoldResult:
    fold_index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_params: dict
    train_metrics: dict
    test_metrics: dict
    train_sharpe: float
    test_sharpe: float
    overfit_ratio: float  # train_sharpe / test_sharpe


class WFOResults:
    """Aggregate walk-forward optimization results across folds."""

    OVERFIT_THRESHOLD = 2.0

    def __init__(self) -> None:
        self._folds: list[FoldResult] = []

    @property
    def folds(self) -> list[FoldResult]:
        return list(self._folds)

    def add_fold(self, fold: FoldResult) -> None:
        self._folds.append(fold)

    def aggregate(self) -> dict:
        """Compute aggregate metrics across all folds."""
        if not self._folds:
            return {
                "n_folds": 0,
                "oos_sharpe_avg": 0.0,
                "avg_overfit_ratio": 0.0,
                "is_overfit": False,
            }

        oos_sharpes = [f.test_sharpe for f in self._folds]
        overfit_ratios = [f.overfit_ratio for f in self._folds]
        oos_win_rates = [f.test_metrics.get("win_rate", 0) for f in self._folds]
        oos_pf = [f.test_metrics.get("profit_factor", 0) for f in self._folds]
        oos_dd = [f.test_metrics.get("max_drawdown_pct", 0) for f in self._folds]

        avg_overfit = sum(overfit_ratios) / len(overfit_ratios)

        return {
            "n_folds": len(self._folds),
            "oos_sharpe_avg": sum(oos_sharpes) / len(oos_sharpes),
            "oos_sharpe_best": max(oos_sharpes),
            "oos_sharpe_worst": min(oos_sharpes),
            "avg_overfit_ratio": avg_overfit,
            "oos_win_rate_avg": sum(oos_win_rates) / len(oos_win_rates),
            "oos_profit_factor_avg": sum(oos_pf) / len(oos_pf),
            "oos_max_drawdown_worst": min(oos_dd),  # most negative = worst
            "is_overfit": avg_overfit > self.OVERFIT_THRESHOLD,
        }

    def is_overfit(self) -> bool:
        """True if average overfit ratio > 2.0."""
        if not self._folds:
            return False
        avg = sum(f.overfit_ratio for f in self._folds) / len(self._folds)
        return avg > self.OVERFIT_THRESHOLD

    def summary(self) -> str:
        """Formatted text summary of WFO results."""
        agg = self.aggregate()
        lines = [
            "=" * 60,
            "WALK-FORWARD OPTIMIZATION RESULTS",
            "=" * 60,
            f"Folds:               {agg['n_folds']:>12d}",
            f"Avg OOS Sharpe:      {agg.get('oos_sharpe_avg', 0):>12.2f}",
            f"Best OOS Sharpe:     {agg.get('oos_sharpe_best', 0):>12.2f}",
            f"Worst OOS Sharpe:    {agg.get('oos_sharpe_worst', 0):>12.2f}",
            f"Avg overfit ratio:   {agg.get('avg_overfit_ratio', 0):>12.2f}",
            f"Avg OOS win rate:    {agg.get('oos_win_rate_avg', 0) * 100:>11.1f}%",
            f"Avg OOS PF:          {agg.get('oos_profit_factor_avg', 0):>12.2f}",
            "",
        ]
        verdict = "OVERFIT DETECTED" if agg.get("is_overfit") else "PASS"
        lines.append(f"Overfit verdict:     {verdict:>12s}")
        lines.append("=" * 60)

        # Per-fold detail
        lines.append("")
        lines.append("Per-fold breakdown:")
        for f in self._folds:
            lines.append(
                f"  Fold {f.fold_index}: "
                f"train={f.train_start}→{f.train_end} "
                f"test={f.test_start}→{f.test_end} | "
                f"IS Sharpe={f.train_sharpe:.2f} "
                f"OOS Sharpe={f.test_sharpe:.2f} "
                f"ratio={f.overfit_ratio:.2f}"
            )

        return "\n".join(lines)

    def to_csv(self, path: str) -> None:
        """Write fold-level results to CSV."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "fold", "train_start", "train_end", "test_start", "test_end",
                "train_sharpe", "test_sharpe", "overfit_ratio", "best_params",
            ])
            for fold in self._folds:
                w.writerow([
                    fold.fold_index,
                    fold.train_start, fold.train_end,
                    fold.test_start, fold.test_end,
                    f"{fold.train_sharpe:.4f}",
                    f"{fold.test_sharpe:.4f}",
                    f"{fold.overfit_ratio:.4f}",
                    str(fold.best_params),
                ])
