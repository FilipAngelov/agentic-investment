"""Performance analytics: Sharpe, win rate, profit factor."""

from __future__ import annotations

import math
from collections import defaultdict


def compute_metrics(trades: list[dict], equity: float) -> dict:
    """Compute all performance metrics from trade list.

    Parameters
    ----------
    trades : list[dict]
        Closed trade rows from ``query_trades()``.  Each dict must have at
        least ``pnl`` (float) and ``exit_time`` (int, epoch seconds).
    equity : float
        Starting equity for return calculations.

    Returns dict with graduation-relevant metrics.
    """
    if not trades or equity <= 0:
        return _empty_metrics()

    # Filter to closed trades with pnl
    closed = [t for t in trades if t.get("pnl") is not None and t.get("exit_time") is not None]
    if not closed:
        return _empty_metrics()

    pnls = [t["pnl"] for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    count = len(pnls)
    total_pnl = sum(pnls)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / count if count else 0.0

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    avg_win_loss_ratio = avg_win / abs(avg_loss) if avg_loss != 0 else float("inf") if avg_win > 0 else 0.0

    # Max consecutive losses
    max_consec = _max_consecutive_losses(pnls)

    # Expectancy
    expectancy = total_pnl / count if count else 0.0

    # Daily series
    daily = _daily_pnl_series(closed)
    daily_pnls = [p for _, p in daily]
    daily_returns = [p / equity for p in daily_pnls]
    trading_days = len(daily)

    total_return_pct = (total_pnl / equity) * 100.0
    max_dd = _max_drawdown(daily_pnls, equity)
    sharpe = _sharpe_ratio(daily_returns)
    sortino = _sortino_ratio(daily_returns)

    daily_std = _std(daily_returns) * 100.0 if len(daily_returns) >= 2 else 0.0

    worst_day_ret = min(daily_returns) if daily_returns else 0.0
    max_single_day_loss_pct = abs(worst_day_ret) * 100.0

    var_95 = _var_95(daily_returns)

    ann_return = (total_return_pct / trading_days * 252) if trading_days > 0 else 0.0
    calmar = ann_return / max_dd if max_dd > 0 else float("inf") if ann_return > 0 else 0.0

    trades_per_day = count / trading_days if trading_days > 0 else 0.0

    return {
        "count": count,
        "total_pnl": total_pnl,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_win_loss_ratio": avg_win_loss_ratio,
        "max_consecutive_losses": max_consec,
        "expectancy": expectancy,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_dd,
        "calmar_ratio": calmar,
        "daily_returns": daily_returns,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "daily_pnl_std_pct": daily_std,
        "max_single_day_loss_pct": max_single_day_loss_pct,
        "var_95_pct": var_95,
        "trades_per_day": trades_per_day,
        "best_trade_pnl": max(pnls),
        "worst_trade_pnl": min(pnls),
    }


def compute_metrics_by_group(
    trades: list[dict], equity: float, group_key: str
) -> dict[str, dict]:
    """Group trades by *group_key* and compute metrics per group."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        key = t.get(group_key) or "unknown"
        groups[key].append(t)
    return {k: compute_metrics(v, equity) for k, v in groups.items()}


# ── internal helpers ─────────────────────────────────────────────


def _daily_pnl_series(trades: list[dict]) -> list[tuple[int, float]]:
    """Aggregate trades into daily PnL buckets. Returns (date_int, pnl) pairs sorted by date."""
    buckets: dict[int, float] = defaultdict(float)
    for t in trades:
        # Convert epoch seconds to date integer YYYYMMDD
        exit_ts = t["exit_time"]
        day = _epoch_to_dateint(exit_ts)
        buckets[day] += t["pnl"]
    return sorted(buckets.items())


def _epoch_to_dateint(ts: int) -> int:
    """Convert Unix epoch seconds to YYYYMMDD integer (UTC)."""
    import datetime as _dt
    d = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).date()
    return d.year * 10000 + d.month * 100 + d.day


def _max_drawdown(daily_pnls: list[float], equity: float) -> float:
    """Peak-to-trough max drawdown as percentage of equity."""
    if not daily_pnls:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in daily_pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return (max_dd / equity) * 100.0


def _sharpe_ratio(daily_returns: list[float]) -> float:
    """Annualized Sharpe = mean/std × √252. Returns 0.0 if < 2 days."""
    if len(daily_returns) < 2:
        return 0.0
    mu = sum(daily_returns) / len(daily_returns)
    std = _std(daily_returns)
    if std == 0:
        return 0.0
    return (mu / std) * math.sqrt(252)


def _sortino_ratio(daily_returns: list[float]) -> float:
    """Annualized Sortino = mean/downside_std × √252."""
    if len(daily_returns) < 2:
        return 0.0
    mu = sum(daily_returns) / len(daily_returns)
    downside = [r for r in daily_returns if r < 0]
    if not downside:
        return float("inf") if mu > 0 else 0.0
    down_std = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
    if down_std == 0:
        return 0.0
    return (mu / down_std) * math.sqrt(252)


def _var_95(daily_returns: list[float]) -> float:
    """Value at Risk (95%) — 5th percentile of daily returns as positive %."""
    if not daily_returns:
        return 0.0
    sorted_r = sorted(daily_returns)
    idx = max(0, int(len(sorted_r) * 0.05))
    return abs(sorted_r[idx]) * 100.0


def _max_consecutive_losses(pnls: list[float]) -> int:
    streak = 0
    worst = 0
    for p in pnls:
        if p <= 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


def _std(values: list[float]) -> float:
    """Sample standard deviation."""
    n = len(values)
    if n < 2:
        return 0.0
    mu = sum(values) / n
    return math.sqrt(sum((x - mu) ** 2 for x in values) / (n - 1))


def _empty_metrics() -> dict:
    return {
        "count": 0, "total_pnl": 0.0, "win_count": 0, "loss_count": 0,
        "win_rate": 0.0, "profit_factor": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "avg_win_loss_ratio": 0.0, "max_consecutive_losses": 0, "expectancy": 0.0,
        "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "calmar_ratio": 0.0,
        "daily_returns": [], "sharpe_ratio": 0.0, "sortino_ratio": 0.0,
        "daily_pnl_std_pct": 0.0, "max_single_day_loss_pct": 0.0, "var_95_pct": 0.0,
        "trades_per_day": 0.0, "best_trade_pnl": 0.0, "worst_trade_pnl": 0.0,
    }
