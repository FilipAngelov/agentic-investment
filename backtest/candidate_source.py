"""Backtest candidate source: universe selection by dollar volume."""

from __future__ import annotations

from backtest.data_feed import HistoricalDataFeed


class BacktestCandidateSource:
    """Rank symbols by 20-day average dollar volume, reconstitute monthly."""

    def __init__(self, feed: HistoricalDataFeed, top_n: int = 50) -> None:
        self._feed = feed
        self._top_n = top_n
        self._cache: list[str] = []
        self._cache_month: int = -1  # YYYYMM

    def get_candidates(self, ts: int) -> list[str]:
        """Return top-N symbols by dollar volume. Reconstitutes on new month."""
        import datetime as _dt

        dt = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc)
        month_key = dt.year * 100 + dt.month

        if month_key != self._cache_month:
            self._reconstitute(ts)
            self._cache_month = month_key

        return list(self._cache)

    def _reconstitute(self, ts: int) -> None:
        """Rank all symbols by 20-day average dollar volume."""
        scores: dict[str, float] = {}

        for symbol in self._feed.all_symbols():
            bars = self._feed.get_bars_up_to(symbol, "1d", ts, lookback=20)
            if len(bars) < 5:
                continue
            total_dv = sum(b["close"] * b["volume"] for b in bars)
            scores[symbol] = total_dv / len(bars)

        ranked = sorted(scores, key=lambda s: scores[s], reverse=True)
        self._cache = ranked[: self._top_n]
