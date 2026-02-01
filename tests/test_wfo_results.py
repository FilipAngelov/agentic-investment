"""Tests for backtest/wfo_results.py."""

import os
import tempfile

import pytest

from backtest.wfo_results import FoldResult, WFOResults


def _make_fold(idx: int, train_sharpe: float, test_sharpe: float) -> FoldResult:
    if abs(test_sharpe) > 1e-9:
        ratio = train_sharpe / test_sharpe
    else:
        ratio = float("inf") if train_sharpe > 0 else 0.0
    return FoldResult(
        fold_index=idx,
        train_start="2024-01-01",
        train_end="2024-12-31",
        test_start="2025-01-01",
        test_end="2025-03-31",
        best_params={"stop_k1": 2.0},
        train_metrics={"sharpe_ratio": train_sharpe, "win_rate": 0.6, "profit_factor": 1.5, "max_drawdown_pct": -5.0},
        test_metrics={"sharpe_ratio": test_sharpe, "win_rate": 0.5, "profit_factor": 1.2, "max_drawdown_pct": -8.0},
        train_sharpe=train_sharpe,
        test_sharpe=test_sharpe,
        overfit_ratio=ratio,
    )


class TestWFOResults:
    def test_empty_aggregate(self):
        r = WFOResults()
        agg = r.aggregate()
        assert agg["n_folds"] == 0
        assert not agg["is_overfit"]

    def test_not_overfit(self):
        r = WFOResults()
        r.add_fold(_make_fold(0, 1.5, 1.2))  # ratio 1.25
        r.add_fold(_make_fold(1, 1.8, 1.5))  # ratio 1.2
        assert not r.is_overfit()
        agg = r.aggregate()
        assert agg["n_folds"] == 2
        assert not agg["is_overfit"]

    def test_overfit_detected(self):
        r = WFOResults()
        r.add_fold(_make_fold(0, 3.0, 0.5))  # ratio 6.0
        r.add_fold(_make_fold(1, 2.5, 0.8))  # ratio 3.125
        assert r.is_overfit()
        agg = r.aggregate()
        assert agg["is_overfit"]
        assert agg["avg_overfit_ratio"] > 2.0

    def test_summary_contains_verdict(self):
        r = WFOResults()
        r.add_fold(_make_fold(0, 1.5, 1.2))
        s = r.summary()
        assert "PASS" in s
        assert "WALK-FORWARD" in s

    def test_summary_overfit_verdict(self):
        r = WFOResults()
        r.add_fold(_make_fold(0, 5.0, 0.5))
        s = r.summary()
        assert "OVERFIT DETECTED" in s

    def test_to_csv(self):
        r = WFOResults()
        r.add_fold(_make_fold(0, 1.5, 1.2))
        r.add_fold(_make_fold(1, 2.0, 1.0))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "folds.csv")
            r.to_csv(path)
            assert os.path.exists(path)
            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 3  # header + 2 folds

    def test_aggregate_metrics(self):
        r = WFOResults()
        r.add_fold(_make_fold(0, 2.0, 1.0))
        r.add_fold(_make_fold(1, 1.0, 1.0))
        agg = r.aggregate()
        assert agg["oos_sharpe_avg"] == pytest.approx(1.0)
        assert agg["oos_sharpe_best"] == pytest.approx(1.0)
        assert agg["oos_sharpe_worst"] == pytest.approx(1.0)
